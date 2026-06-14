"""Quick test of the eligibility engine against the synthetic personas,
without needing Streamlit or an LLM API key."""

import json

# Re-import the engine functions from app.py by loading the module manually,
# but since app.py calls st.* at module level, we extract just the logic we need.

with open("programs.json") as f:
    PROGRAMS_DATA = json.load(f)

with open("personas.json") as f:
    PERSONAS_DATA = json.load(f)


def get_poverty_line_monthly(household_size, guidelines):
    key = str(min(max(household_size, 1), 6))
    annual = guidelines.get(key)
    if annual is None:
        return None
    return annual / 12


def evaluate_criterion(criterion, profile, guidelines):
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


def evaluate_program(program, profile, guidelines):
    criterion_results = [evaluate_criterion(c, profile, guidelines) for c in program["criteria"]]
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
        "confidence": confidence,
        "criteria_results": criterion_results,
    }


def run_eligibility_check(profile):
    guidelines = PROGRAMS_DATA.get("poverty_guidelines_annual", {})
    return [evaluate_program(p, profile, guidelines) for p in PROGRAMS_DATA.get("programs", [])]


# ── Run for each persona ──
for persona in PERSONAS_DATA["personas"]:
    print("=" * 60)
    print(f"PERSONA: {persona['name']} - {persona['summary']}")
    print("=" * 60)
    results = run_eligibility_check(persona["profile"])
    for r in results:
        print(f"\n  {r['name']} -> {r['confidence']}")
        for c in r["criteria_results"]:
            if c["status"] == "not_applicable":
                continue
            print(f"    [{c['status'].upper():9s}] {c['label']}: {c['reason']}")
    print()

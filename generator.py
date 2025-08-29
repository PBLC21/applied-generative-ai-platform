# generator.py — V0.7.3
# - Multiple Choice: generate with keys, then split out a separate Answer Key sheet.
# - Removes any inline "**Answer Key:**" / "**Clave de respuestas:**" lines from questions.
# - Keeps Short Answer / Open Response questions-only.

import json, re
from typing import List, Dict, Tuple

try:
    from content_llm import LLMClient  # type: ignore
except Exception:
    LLMClient = None

def _llm_complete_compat(client, prompt: str, **kwargs) -> str:
    if hasattr(client, "complete"): return client.complete(prompt, **kwargs)
    if hasattr(client, "chat"): return client.chat(prompt, **kwargs)
    if hasattr(client, "run"): return client.run(prompt, **kwargs)
    if hasattr(client, "__call__"): return client(prompt, **kwargs)
    raise AttributeError("LLMClient lacks known methods.")

# -------- Alignment judge (unchanged) --------
_ALIGN_JUDGE_TEMPLATE = """
You are a strict TEKS alignment checker.
TEKS code: {code}
TEKS description: "{desc}"
Document type: {doc_type}
Document content (Markdown):
<<<
{content}
>>>
Return ONLY JSON:
{{
  "score": float,
  "issues": [ "string", ... ],
  "non_aligned_items": [ "Q1", "Q2", "P1", ... ]
}}
"""

def judge_alignment(teks_code: str, teks_description: str, content: str, doc_type: str) -> Dict:
    if LLMClient is None:
        return {"score": 0.0, "issues": ["No LLM client."], "non_aligned_items": []}
    client = LLMClient()
    raw = _llm_complete_compat(client, _ALIGN_JUDGE_TEMPLATE.format(
        code=teks_code, desc=teks_description or "(none provided)", doc_type=doc_type, content=content
    ), max_tokens=800, temperature=0.0)
    text = str(raw).strip()
    try:
        start, end = text.find("{"), text.rfind("}")
        if start!=-1 and end!=-1 and end>start: text = text[start:end+1]
        data = json.loads(text)
        data.setdefault("score", 0.0); data.setdefault("issues", []); data.setdefault("non_aligned_items", [])
        if not isinstance(data["issues"], list): data["issues"] = [str(data["issues"])]
        if not isinstance(data["non_aligned_items"], list): data["non_aligned_items"] = [str(data["non_aligned_items"])]
        return data
    except Exception:
        return {"score": 0.0, "issues": ["Could not parse judge output."], "non_aligned_items": []}

# -------- topical guardrails for 3.6A --------
BANNED_36A = re.compile(r"\b(fraction|equivalent|numerator|denominator|decimal|money|dollar|add(ition)?|subtract(ion)?)\b", re.I)
REQUIRED_36A = ["cone","cylinder","sphere","triangular prism","rectangular prism","cube"]
def _needs_fix_36A(text: str) -> bool:
    if BANNED_36A.search(text): return True
    hits = sum(1 for w in REQUIRED_36A if re.search(rf"\b{re.escape(w)}\b", text, re.I))
    return hits < 3

# -------- Lesson plan generation (unchanged) --------
_LESSON_SYSTEM = "You are a master elementary math coach who writes concise, teacher-ready Markdown."

def generate_lesson_plan_ai(grade_label: str, teks_code: str, teks_description: str,
                            bilingual: bool, teacher_notes: str, strict_align: bool=True) -> str:
    if LLMClient is None: raise RuntimeError("AI-only mode: content_llm.LLMClient not found.")
    client = LLMClient(system=_LESSON_SYSTEM)

    neg_clause = "Avoid fractions, money contexts, and general addition/subtraction content." if teks_code.strip().upper()=="3.6A" else ""

    prompt = f"""
Create a teacher-ready LESSON PLAN aligned ONLY to TEKS {teks_code} for {grade_label} Math.
TEKS description: "{teks_description}"
Use formal geometric language. {neg_clause}
If bilingual={bilingual}, include Spanish where appropriate.

Sections in this exact order:
# Lesson Plan
In this lesson, students will work toward TEKS {teks_code}.
**Objective (EN)** (1 bullet)
{"**Objetivo (ES)** (1 bullet)" if bilingual else ""}
**Academic Vocabulary** (3–6 terms EN; {"add ES line" if bilingual else "EN only"})
**Materials** (4–8 bullets)
**Mini Lesson** (2–3 bullets; include 1 worked example)
**I Do (Model)** (2–3 bullets with teacher moves)
**We Do (Guided)** (2–3 bullets with prompts)
**Checks for Understanding** (3 prompts)
**You Do (Independent)** (1 bullet describing 6–8 short items aligned to {teks_code})
**Exit Ticket** (1 bullet)

Teacher Notes & Attachments:
{teacher_notes or "(none)"}

Return Markdown only.
""".strip()

    text = _llm_complete_compat(client, prompt, max_tokens=1400, temperature=0.2).strip()

    if teks_code.strip().upper() == "3.6A" and _needs_fix_36A(text):
        fix = f"""
Revise the lesson to strictly align to TEKS {teks_code}: "{teks_description}".
- Focus on classifying/sorting 3-D figures: cones, cylinders, spheres, triangular prisms, rectangular prisms, cubes.
- Use attributes and formal geometric language (faces, edges, vertices, curved/flat surfaces).
- Do NOT include fractions, money, or general addition/subtraction.
Keep the same section headers and order. Return Markdown only.

Original:
<<<
{text}
>>>
"""
        text = _llm_complete_compat(client, fix, max_tokens=1200, temperature=0.2).strip()

    if not strict_align: return text
    threshold = 0.90 if teks_description else 0.70
    report = judge_alignment(teks_code, teks_description, text, "lesson")
    if report.get("score", 0.0) < threshold:
        issues = "; ".join(report.get("issues", [])) or "General drift."
        rev = f"""
Revise the LESSON PLAN so it strictly aligns to TEKS {teks_code}: "{teks_description}".
Fix these issues: {issues}
Keep the SAME section headers and order.
Return Markdown only.

Original:
<<<
{text}
>>>
"""
        text = _llm_complete_compat(client, rev, max_tokens=1200, temperature=0.2).strip()
    return text

# -------- Worksheet generation --------

_WS_COMMON_HEADER = """# Math {grade} — {teks} Worksheet
\\1 {teks}
"""

_WS_SA_BODY = """
**Problems (EN)**
- Q1. ...
- Q2. ...
- Q3. ...
- Q4. ...
- Q5. ...
- Q6. ...
- Q7. ...
- Q8. ...

**Problemas (ES)**
- P1. ...
- P2. ...
- P3. ...
- P4. ...
- P5. ...
- P6. ...
- P7. ...
- P8. ...
"""

_WS_MC_BODY = """
**Problems (EN)**
- Q1. <stem>
  - A. ...
  - B. ...
  - C. ...
  - D. ...
- Q2. <stem>
  - A. ...
  - B. ...
  - C. ...
  - D. ...
- Q3. <stem>
  - A. ...
  - B. ...
  - C. ...
  - D. ...
- Q4. <stem>
  - A. ...
  - B. ...
  - C. ...
  - D. ...
- Q5. <stem>
  - A. ...
  - B. ...
  - C. ...
  - D. ...
- Q6. <stem>
  - A. ...
  - B. ...
  - C. ...
  - D. ...
- Q7. <stem>
  - A. ...
  - B. ...
  - C. ...
  - D. ...
- Q8. <stem>
  - A. ...
  - B. ...
  - C. ...
  - D. ...

**Answer Key (EN)**
Q1: A; Q2: B; Q3: C; Q4: D; Q5: A; Q6: B; Q7: C; Q8: D

**Problemas (ES)**
- P1. <enunciado>
  - A. ...
  - B. ...
  - C. ...
  - D. ...
- P2. <enunciado>
  - A. ...
  - B. ...
  - C. ...
  - D. ...
- P3. <enunciado>
  - A. ...
  - B. ...
  - C. ...
  - D. ...
- P4. <enunciado>
  - A. ...
  - B. ...
  - C. ...
  - D. ...
- P5. <enunciado>
  - A. ...
  - B. ...
  - C. ...
  - D. ...
- P6. <enunciado>
  - A. ...
  - B. ...
  - C. ...
  - D. ...
- P7. <enunciado>
  - A. ...
  - B. ...
  - C. ...
  - D. ...
- P8. <enunciado>
  - A. ...
  - B. ...
  - C. ...
  - D. ...

**Clave de respuestas (ES)**
P1: A; P2: B; P3: C; P4: D; P5: A; P6: B; P7: C; P8: D
"""

_WS_OR_BODY = """
**Problems (EN)**
- Q1. Explain...
- Q2. Explain...
- Q3. Explain...
- Q4. Explain...
- Q5. Explain...
- Q6. Explain...
- Q7. Explain...
- Q8. Explain...

**Problemas (ES)**
- P1. Explica...
- P2. Explica...
- P3. Explica...
- P4. Explica...
- P5. Explica...
- P6. Explica...
- P7. Explica...
- P8. Explica...
"""

def _type_mode(question_types: List[str]) -> str:
    q = [t.lower() for t in (question_types or [])]
    if "multiple choice" in q: return "mc"
    if "short answer" in q: return "sa"
    if "open response" in q: return "or"
    return "sa"

def _mc_valid(text: str) -> bool:
    if len(re.findall(r"-\s*Q\d+\.", text)) != 8: return False
    if len(re.findall(r"-\s*P\d+\.", text)) != 8: return False
    for tag in ["Q","P"]:
        for i in range(1,9):
            pat = rf"-\s*{tag}{i}\..*?(?:(?:-\s*{tag}{i+1}\.)|(?:\*\*Answer Key)|(?:\*\*Clave)|\Z)"
            m = re.search(pat, text, re.S|re.I)
            if not m: return False
            block = m.group(0)
            if not all(re.search(rf"^\s*-\s*[ABCD]\.", block, re.M) for _ in "ABCD"):
                return False
    return True

def _strip_placeholders_mc(text: str) -> str:
    return re.sub(r"<stem>|<enunciado>", "Rewrite to a concrete, TEKS-aligned question.", text)

def _extract_keys_and_strip(text: str) -> Tuple[str, List[str], List[str]]:
    """
    Returns (questions_only_text, en_keys[8] or [], es_keys[8] or [])
    - Removes '**Answer Key (EN)** ...' and '**Clave de respuestas (ES)** ...' sections.
    - Also removes per-item '**Answer Key:** X' / '**Clave de respuestas:** X' lines if present.
    """
    questions = text

    # Collect consolidated EN keys
    en_keys = []
    m = re.search(r"\*\*Answer Key\s*\(EN\)\*\*([\s\S]+?)(?:\n\*\*Problemas|$)", questions, re.I)
    if m:
        block = m.group(1)
        en_keys = re.findall(r"Q(\d+)\s*:\s*([ABCD])", block, re.I)
        en_map = {int(k): v.upper() for k, v in en_keys if k.isdigit()}
        en_keys = [en_map.get(i, "") for i in range(1,9)]
        questions = questions.replace(m.group(0), "")  # drop whole EN key section

    # Collect consolidated ES keys
    es_keys = []
    m2 = re.search(r"\*\*Clave de respuestas\s*\(ES\)\*\*([\s\S]+?)$", questions, re.I)
    if m2:
        block = m2.group(1)
        es_keys = re.findall(r"P(\d+)\s*:\s*([ABCD])", block, re.I)
        es_map = {int(k): v.upper() for k, v in es_keys if k.isdigit()}
        es_keys = [es_map.get(i, "") for i in range(1,9)]
        questions = questions.replace(m2.group(0), "")

    # Handle per-item inline keys (EN)
    def _pull_inline_keys(tag_letter: str, label_regex: str, keys_list: List[str]) -> Tuple[str,List[str]]:
        txt = questions
        found = keys_list[:] if keys_list else ["" for _ in range(8)]
        for i in range(1,9):
            pat = rf"(-\s*{tag_letter}{i}\..*?)(?:\n\*\*{label_regex}\*\*\s*([ABCD]))"
            m = re.search(pat, txt, re.S|re.I)
            if m:
                # record and remove the inline key
                key = m.group(2).upper()
                found[i-1] = key
                txt = txt[:m.start(2)-4] + txt[m.end(2):]  # remove '**Answer Key:** X' line
        return txt, found

    if not any(en_keys): questions, en_keys = _pull_inline_keys("Q", r"Answer Key\s*:", en_keys)
    if not any(es_keys): questions, es_keys = _pull_inline_keys("P", r"Clave de respuestas\s*:", es_keys)

    # Clean double blank lines
    questions = re.sub(r"\n{3,}", "\n\n", questions).strip()
    return questions, en_keys, es_keys

_WS_BASE_COMMON = """You are generating a student-ready WORKSHEET aligned ONLY to TEKS {teks} for {grade} Math.
TEKS description: "{desc}"

General constraints for ALL types:
- EXACTLY 8 English items (Q1–Q8) and 8 Spanish items (P1–P8). No placeholders like "aligned to..." or "<stem>".
- Mirror difficulty across languages. Items must directly assess TEKS {teks}.
- Use Teacher Notes & Attachments when relevant.
- If TEKS is 3.6A: focus on classifying/sorting cones, cylinders, spheres, triangular prisms, rectangular prisms, and cubes by attributes (faces, edges, vertices, curved/flat surfaces). Avoid fractions/money/add/sub contexts.

Teacher Notes & Attachments:
{notes}
"""

_WS_SA_INSTR = "\nQUESTION TYPE: Short Answer only.\nOutput format (exact):" + _WS_COMMON_HEADER + _WS_SA_BODY
_WS_MC_INSTR = "\nQUESTION TYPE: Multiple Choice only.\nEach item must have A–D options with ONE correct answer. Include consolidated **Answer Key (EN)** and **Clave de respuestas (ES)** after the question sections.\nOutput format (exact):" + _WS_COMMON_HEADER + _WS_MC_BODY
_WS_OR_INSTR = "\nQUESTION TYPE: Open Response only.\nNo options and no blanks like '___'. Require explanation/justification.\nOutput format (exact):" + _WS_COMMON_HEADER + _WS_OR_BODY

_WS_FIX = """Revise the worksheet to match the required QUESTION TYPE and strict TEKS alignment for {teks}: "{desc}".
Rules:
- Keep the same section headers.
- Ensure exactly 8 EN (Q1–Q8) and 8 ES (P1–P8).
- For Multiple Choice: each item shows A–D options; put consolidated keys in **Answer Key (EN)** and **Clave de respuestas (ES)**.
- For Short/Open Response: no options; no inline answers.
Return ONLY the corrected Markdown."""

def generate_worksheet_ai(grade_label: str, teks_code: str, teks_description: str,
                          question_types: List[str], bilingual: bool,
                          teacher_notes: str, strict_align: bool=True):
    if LLMClient is None: raise RuntimeError("AI-only mode: content_llm.LLMClient not found.")
    client = LLMClient()
    mode = _type_mode(question_types)

    base_common = _WS_BASE_COMMON.format(teks=teks_code, grade=grade_label,
                                         desc=teks_description or "no description",
                                         notes=teacher_notes or "(none)")
    if mode == "mc":
        prompt = base_common + _WS_MC_INSTR
    elif mode == "or":
        prompt = base_common + _WS_OR_INSTR
    else:
        prompt = base_common + _WS_SA_INSTR

    text = _llm_complete_compat(client, prompt, max_tokens=1800, temperature=0.2).strip()
    text = _strip_placeholders_mc(text) if mode == "mc" else text

    # Basic format & topical checks
    bad_format = (len(re.findall(r"-\s*Q\d+\.", text)) != 8 or len(re.findall(r"-\s*P\d+\.", text)) != 8)
    if mode == "mc" and not _mc_valid(text): bad_format = True
    if mode == "sa" and re.search(r"^\s*-\s*[ABCD]\.", text, re.M): bad_format = True
    if mode == "or" and (re.search(r"^\s*-\s*[ABCD]\.", text, re.M) or "___" in text): bad_format = True
    if teks_code.strip().upper() == "3.6A" and _needs_fix_36A(text): bad_format = True

    if bad_format:
        text = _llm_complete_compat(client, _WS_FIX.format(teks=teks_code, desc=teks_description),
                                    max_tokens=1400, temperature=0.2).strip()

    if strict_align:
        threshold = 0.90 if teks_description else 0.70
        report = judge_alignment(teks_code, teks_description, text, "worksheet")
        score = float(report.get("score", 0.0)); non_aligned = report.get("non_aligned_items", []) or []
        if score < threshold or non_aligned:
            issues = "; ".join(report.get("issues", []))
            if non_aligned: issues = (issues + "; " if issues else "") + f"Non-aligned items: {', '.join(non_aligned)}."
            fix = _WS_FIX.format(teks=teks_code, desc=teks_description) + f"\n\nIssues to fix: {issues}"
            text = _llm_complete_compat(client, fix, max_tokens=1400, temperature=0.2).strip()

    # Build separate Answer Key sheet by extracting keys and stripping them from questions
    answer_key_md = ""
    if mode == "mc":
        questions_only, en_keys, es_keys = _extract_keys_and_strip(text)
        if any(en_keys) or any(es_keys):
            lines = [f"# Answer Key — Math {grade_label} — {teks_code}", ""]
            if any(en_keys):
                ek = "; ".join([f"Q{i}: {en_keys[i-1] or '?'}" for i in range(1,9)])
                lines += ["**Answer Key (EN)**", ek, ""]
            if any(es_keys):
                sk = "; ".join([f"P{i}: {es_keys[i-1] or '?'}" for i in range(1,9)])
                lines += ["**Clave de respuestas (ES)**", sk, ""]
            answer_key_md = "\n".join(lines).strip()
        text = questions_only  # ensure worksheet has no keys inline

    return {"worksheet_md": text, "answer_key_md": answer_key_md}

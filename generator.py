# generator.py — AI-backed TEKS generator
from __future__ import annotations
try:
    from dotenv import load_dotenv; load_dotenv()
except Exception:
    pass

import os, json, zipfile
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Tuple, Optional

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch
from reportlab.lib.utils import simpleSplit

@dataclass
class Outputs:
    lesson_plan_fp: str
    worksheet_fp: str
    answer_key_fp: str
    zip_fp: str

def _ensure_dir(p: str): os.makedirs(p, exist_ok=True)
def _today_short(): return datetime.now().strftime("%y%m%d")
def _norm_grade(g: str) -> str:
    g=(g or "").strip()
    return "K" if g.lower() in {"k","kinder","kindergarten"} else g
def _base(grade: str, teks_code: str) -> str:
    code=(teks_code or "").replace("/", "-").replace(" ", "").strip()
    return f"G{_norm_grade(grade)}-{code}_{_today_short()}"

def _title(c: canvas.Canvas, text: str) -> float:
    w,h=letter; c.setFont("Helvetica-Bold", 18); c.drawString(1*inch, h-1*inch, text); return h-1.25*inch
def _kv(c: canvas.Canvas, items: List[Tuple[str,str]], y: float) -> float:
    c.setFont("Helvetica", 11)
    for k,v in items: c.drawString(1*inch, y, f"{k}: {v}"); y -= 14
    return y-6
def _h(c: canvas.Canvas, text: str, y: float) -> float:
    c.setFont("Helvetica-Bold", 13); c.drawString(1*inch, y, text); return y-16
def _wrap(c: canvas.Canvas, text: str, x: float, y: float, maxw: float, leading: float=14) -> float:
    if not text: return y
    lines=simpleSplit(text, c._fontname, c._fontsize, maxw)
    for ln in lines: c.drawString(x, y, ln); y -= leading
    return y
def _newpage_if(c: canvas.Canvas, y: float, min_y: float=1*inch) -> float:
    if y < min_y: c.showPage(); return _title(c, "Continued")
    return y

# ---------- AI plumbing ----------
def _openai_chat(messages: List[Dict], model: str="gpt-4o-mini", temperature: float=0.7) -> Optional[str]:
    api_key=os.getenv("OPENAI_API_KEY")
    if not api_key: return None
    mdl=os.getenv("OPENAI_MODEL", model)
    try:
        from openai import OpenAI
        client=OpenAI(api_key=api_key)
        resp=client.chat.completions.create(model=mdl, messages=messages, temperature=temperature)
        return resp.choices[0].message.content
    except Exception:
        try:
            import openai
            openai.api_key=api_key
            resp=openai.ChatCompletion.create(model=mdl, messages=messages, temperature=temperature)
            return resp.choices[0]["message"]["content"]
        except Exception:
            return None

def _ai_make_json(grade:str, subject:str, teks_code:str, bilingual:bool,
                  notes:str, attachments_text:str,
                  teks_description_en:Optional[str], teks_description_es:Optional[str],
                  teks_strand:Optional[str], teks_type:Optional[str]) -> Optional[Dict]:
    wants_story = "story" in (notes or "").lower() or subject.lower().startswith("read")
    sys = (
        "You are an expert K-6 instructional designer in Texas TEKS. "
        "Design lesson plans and worksheets aligned to the TEKS. "
        "Return ONLY JSON."
    )
    user = {
        "grade": _norm_grade(grade),
        "subject": subject,
        "teks_code": teks_code,
        "teks_strand": teks_strand or "",
        "teks_type": teks_type or "",
        "description_en": teks_description_en or "",
        "description_es": teks_description_es or "",
        "bilingual": bool(bilingual),
        "teacher_notes": notes or "",
        "attachments_excerpt": (attachments_text or "")[:6000],
        "require_passage": wants_story,
        "passage_max_words": 300,
        "format_requirements": {
            "lesson_plan_sections": [
                "Objective_EN","Objective_ES","Success_Criteria_EN","Success_Criteria_ES",
                "Academic_Vocabulary","Materials","Mini_Lesson","I_Do","We_Do",
                "Checks_for_Understanding","You_Do","Exit_Ticket"
            ],
            "worksheet": {"questions_EN": 8, "questions_ES": 8, "answer_key": "array aligned to EN questions"},
            "optional_fields": ["Passage_EN","Passage_ES"]
        },
        # Make attachments + teacher notes matter
        "hard_requirements": [
            "Explicitly incorporate relevant details from teacher_notes.",
            "If attachments_excerpt is provided, create at least ONE worksheet item clearly modeled on it (label it 'Q2-like exemplar').",
            "Keep language grade-appropriate; keep math within grade expectations."
        ]
    }
    schema = (
        "Expected JSON schema:\n"
        "{\n"
        '  "lesson_plan": {\n'
        '     "Objective_EN": "string",\n'
        '     "Objective_ES": "string",\n'
        '     "Success_Criteria_EN": ["I can …", "..."],\n'
        '     "Success_Criteria_ES": ["Puedo …", "..."],\n'
        '     "Academic_Vocabulary": ["term", "..."],\n'
        '     "Materials": ["item", "..."],\n'
        '     "Mini_Lesson": "string",\n'
        '     "I_Do": "string",\n'
        '     "We_Do": "string",\n'
        '     "Checks_for_Understanding": ["question", "..."],\n'
        '     "You_Do": "string",\n'
        '     "Exit_Ticket": "string"\n'
        '  },\n'
        '  "worksheet": {\n'
        '     "EN": ["Q1", "Q2", "... 8 items (include one Q2-like exemplar if attachments_excerpt provided)"],\n'
        '     "ES": ["P1", "P2", "... 8 items"],\n'
        '     "answers": ["A", "B", "C", "D", "A", "B", "C", "D"]\n'
        '  },\n'
        '  "Passage_EN": "≤300 words narrative aligned to TEKS (optional)",\n'
        '  "Passage_ES": "≤300 palabras, español (opcional)"\n'
        "}\n"
        "Make Success Criteria observable and student-facing ('I can…')."
    )
    messages = [
        {"role":"system","content":sys},
        {"role":"user","content":f"Return JSON only.\n\n{json.dumps(user, ensure_ascii=False)}\n\n{schema}"}
    ]
    raw=_openai_chat(messages)
    if not raw: return None
    raw=raw.strip()
    if raw.startswith("```"): raw=raw.split("```",2)[1]
    try: return json.loads(raw)
    except Exception:
        try:
            start=raw.find("{"); end=raw.rfind("}")
            if start!=-1 and end!=-1: return json.loads(raw[start:end+1])
        except Exception:
            return None

def _fallback_content(grade:str, subject:str, teks_code:str, bilingual:bool, notes:str) -> Dict:
    vocab = ["attribute","classify","compare","contrast","justify","evidence","represent","analyze"]
    passage_en = "A brief, grade-appropriate passage tied to the TEKS skill."
    passage_es = "Un pasaje breve, apropiado para la edad, vinculado a la habilidad del TEKS."
    return {
        "lesson_plan": {
            "Objective_EN": f"Students will demonstrate understanding of TEKS {teks_code} through modeling and practice.",
            "Objective_ES": f"El alumnado demostrará comprensión del TEKS {teks_code} mediante modelado y práctica.",
            "Success_Criteria_EN": ["I can explain the skill in my own words.","I can solve a grade-level example independently."],
            "Success_Criteria_ES": ["Puedo explicar la habilidad con mis propias palabras.","Puedo resolver un ejemplo de mi grado de forma independiente."],
            "Academic_Vocabulary": vocab,
            "Materials": ["Whiteboard","Printed worksheet","Pencils","Manipulatives or visuals"],
            "Mini_Lesson": "Review prior knowledge, model with a worked example.",
            "I_Do": "Teacher models the solution process, thinking aloud.",
            "We_Do": "Guided practice on 2–3 items with class participation.",
            "Checks_for_Understanding": ["Explain a step you used.","What mistake should we avoid?","How do we know our answer is reasonable?"],
            "You_Do": "Students complete the worksheet independently or in pairs.",
            "Exit_Ticket": "One quick item aligned to the objective."
        },
        "worksheet": {
            "EN": [f"Q{i+1}: Short response tied to {teks_code}." for i in range(8)],
            "ES": [f"P{i+1}: Respuesta breve ligada a {teks_code}." for i in range(8)],
            "answers": [""]*8
        },
        "Passage_EN": passage_en,
        "Passage_ES": passage_es if bilingual else ""
    }

# ---------- PDF writers ----------
def _write_lesson_plan_pdf(fp:str, grade:str, subject:str, teks_code:str, bilingual:bool, data:Dict):
    c=canvas.Canvas(fp, pagesize=letter); y=_title(c, "Lesson Plan")
    y=_kv(c, [("Grade", _norm_grade(grade)), ("Subject", subject), ("TEKS", teks_code), ("Date", datetime.now().strftime("%Y-%m-%d"))], y)
    w,h=letter; margin=1*inch; maxw=w-2*inch
    lp=data["lesson_plan"]

    y=_h(c, "Objective (EN)", y); c.setFont("Helvetica", 11); y=_wrap(c, lp.get("Objective_EN",""), margin, y, maxw); y-=6
    if bilingual:
        y=_newpage_if(c, y); y=_h(c, "Objetivo (ES)", y); c.setFont("Helvetica", 11); y=_wrap(c, lp.get("Objective_ES",""), margin, y, maxw); y-=6

    y=_newpage_if(c, y); y=_h(c, "Success Criteria (EN)", y); c.setFont("Helvetica", 11)
    y=_wrap(c, " • " + "\n • ".join(lp.get("Success_Criteria_EN", [])), margin, y, maxw); y-=6
    if bilingual:
        y=_newpage_if(c, y); y=_h(c, "Criterios de éxito (ES)", y); c.setFont("Helvetica", 11)
        y=_wrap(c, " • " + "\n • ".join(lp.get("Success_Criteria_ES", [])), margin, y, maxw); y-=6

    y=_newpage_if(c, y); y=_h(c, "Academic Vocabulary", y); c.setFont("Helvetica", 11)
    y=_wrap(c, ", ".join(lp.get("Academic_Vocabulary", [])), margin, y, maxw); y-=6
    y=_newpage_if(c, y); y=_h(c, "Materials", y); c.setFont("Helvetica", 11)
    y=_wrap(c, " • " + "\n • ".join(lp.get("Materials", [])), margin, y, maxw); y-=6

    pe=data.get("Passage_EN","") or ""; ps=data.get("Passage_ES","") or ""
    if pe:
        y=_newpage_if(c, y); y=_h(c, "Paired Passage (EN)", y); c.setFont("Helvetica", 11); y=_wrap(c, pe, margin, y, maxw); y-=6
    if bilingual and ps:
        y=_newpage_if(c, y); y=_h(c, "Pasaje vinculado (ES)", y); c.setFont("Helvetica", 11); y=_wrap(c, ps, margin, y, maxw); y-=6

    y=_newpage_if(c, y); y=_h(c, "Mini Lesson", y); c.setFont("Helvetica", 11); y=_wrap(c, lp.get("Mini_Lesson",""), margin, y, maxw); y-=6
    y=_newpage_if(c, y); y=_h(c, "I Do (Model)", y); c.setFont("Helvetica", 11); y=_wrap(c, lp.get("I_Do",""), margin, y, maxw); y-=6
    y=_newpage_if(c, y); y=_h(c, "We Do (Guided)", y); c.setFont("Helvetica", 11); y=_wrap(c, lp.get("We_Do",""), margin, y, maxw); y-=6
    y=_newpage_if(c, y); y=_h(c, "Checks for Understanding", y); c.setFont("Helvetica", 11)
    y=_wrap(c, " • " + "\n • ".join(lp.get("Checks_for_Understanding", [])), margin, y, maxw); y-=6
    y=_newpage_if(c, y); y=_h(c, "You Do (Independent)", y); c.setFont("Helvetica", 11); y=_wrap(c, lp.get("You_Do",""), margin, y, maxw); y-=6
    y=_newpage_if(c, y); y=_h(c, "Exit Ticket", y); c.setFont("Helvetica", 11); y=_wrap(c, lp.get("Exit_Ticket",""), margin, y, maxw)
    c.save()

def _write_worksheet_and_key(ws_fp:str, ak_fp:str, grade:str, subject:str, teks_code:str, bilingual:bool, data:Dict):
    c=canvas.Canvas(ws_fp, pagesize=letter); y=_title(c, "Worksheet / Hoja de trabajo" if bilingual else "Worksheet")
    y=_kv(c, [("TEKS", teks_code), ("Grade", _norm_grade(grade)), ("Subject", subject), ("Date", datetime.now().strftime("%Y-%m-%d"))], y)
    w,h=letter; margin=1*inch; maxw=w-2*inch; c.setFont("Helvetica", 12)

    pe=data.get("Passage_EN","") or ""; ps=data.get("Passage_ES","") or ""
    if pe:
        y=_h(c, "Reading Passage (EN)", y); c.setFont("Helvetica", 11); y=_wrap(c, pe, margin, y, maxw); y-=8

    c.setFont("Helvetica", 12); y=_h(c, "English (EN)", y); c.setFont("Helvetica", 11)
    for i,q in enumerate(data["worksheet"]["EN"], 1):
        y=_newpage_if(c, y); y=_wrap(c, f"{i}. {q}", margin, y, maxw, leading=14); y-=6

    if bilingual:
        c.showPage(); y=_title(c, "Hoja de trabajo (ES)")
        y=_kv(c, [("TEKS", teks_code), ("Grado", _norm_grade(grade)), ("Asignatura", "Lectura" if subject.lower().startswith("read") else "Matemáticas"), ("Fecha", datetime.now().strftime("%Y-%m-%d"))], y)
        if ps:
            y=_h(c, "Pasaje (ES)", y); c.setFont("Helvetica", 11); y=_wrap(c, ps, margin, y, maxw); y-=8
        c.setFont("Helvetica", 12); y=_h(c, "Español (ES)", y); c.setFont("Helvetica", 11)
        for i,q in enumerate(data["worksheet"]["ES"], 1):
            y=_newpage_if(c, y); y=_wrap(c, f"{i}. {q}", margin, y, maxw, leading=14); y-=6
    c.save()

    c=canvas.Canvas(ak_fp, pagesize=letter); y=_title(c, "Answer Key / Clave de respuestas" if bilingual else "Answer Key")
    y=_kv(c, [("TEKS", teks_code), ("Grade", _norm_grade(grade)), ("Subject", subject), ("Date", datetime.now().strftime("%Y-%m-%d"))], y)
    c.setFont("Helvetica", 12)
    for i,ans in enumerate(data["worksheet"].get("answers", []), 1):
        y=_newpage_if(c, y); c.drawString(1*inch, y, f"{i}. {ans}"); y-=16
    c.save()

def generate_all_outputs(
    teks_code:str, grade:str, subject:str="Math", bilingual:bool=True,
    teacher_notes:str="", attachments_text:str="",
    teks_description_en:Optional[str]=None, teks_description_es:Optional[str]=None,
    teks_strand:Optional[str]=None, teks_type:Optional[str]=None,
    out_dir:str="outputs"
) -> Outputs:
    if not teks_code: raise ValueError("teks_code is required")
    _ensure_dir(out_dir); base=_base(grade, teks_code)
    lesson_plan_fp=os.path.join(out_dir, f"{base}_LP.pdf")
    worksheet_fp  =os.path.join(out_dir, f"{base}_WS.pdf")
    answer_key_fp =os.path.join(out_dir, f"{base}_AK.pdf")
    zip_fp        =os.path.join(out_dir, f"{base}.zip")

    data=_ai_make_json(
        grade=grade, subject=subject, teks_code=teks_code, bilingual=bilingual,
        notes=teacher_notes, attachments_text=attachments_text,
        teks_description_en=teks_description_en, teks_description_es=teks_description_es,
        teks_strand=teks_strand, teks_type=teks_type
    )
    if not data or "lesson_plan" not in data or "worksheet" not in data:
        data=_fallback_content(grade, subject, teks_code, bilingual, teacher_notes)

    _write_lesson_plan_pdf(lesson_plan_fp, grade, subject, teks_code, bilingual, data)
    _write_worksheet_and_key(worksheet_fp, answer_key_fp, grade, subject, teks_code, bilingual, data)

    with zipfile.ZipFile(zip_fp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(lesson_plan_fp, os.path.basename(lesson_plan_fp))
        zf.write(worksheet_fp,   os.path.basename(worksheet_fp))
        zf.write(answer_key_fp,  os.path.basename(answer_key_fp))

    return Outputs(lesson_plan_fp=lesson_plan_fp, worksheet_fp=worksheet_fp, answer_key_fp=answer_key_fp, zip_fp=zip_fp)

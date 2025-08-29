# app.py — STAAR AI (V0.7.3)
# - Generates a separate Answer Key PDF for worksheets.
# - Hides alignment report (still enforced internally).
# - Supports TEKS CSV with description_en / description_es.

import os, io, re, zipfile
from datetime import datetime
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
import pandas as pd

try:
    from streamlit_pdf_viewer import pdf_viewer  # type: ignore
    HAS_PDF_VIEWER = True
except Exception:
    HAS_PDF_VIEWER = False

try:
    from PyPDF2 import PdfReader  # type: ignore
    HAS_PYPDF2 = True
except Exception:
    HAS_PYPDF2 = False

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

import generator as gen

try:
    from content_llm import LLMClient  # type: ignore
    HAS_LLMCLIENT = True
except Exception:
    HAS_LLMCLIENT = False

APP_TITLE = "STAAR AI — Lesson & Worksheet Generator (V0.7.3)"
OUTPUT_DIR = Path("./outputs"); OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
load_dotenv(override=True)
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")

GRADE_OPTIONS = ["1st", "2nd", "3rd", "4th", "5th"]
QUESTION_TYPE_OPTIONS = ["Short Answer", "Multiple Choice", "Open Response"]

ATTACH_IMAGE_TYPES = ["png", "jpg", "jpeg", "webp"]
ATTACH_DOC_TYPES = ["pdf", "txt", "md", "csv"]
ATTACH_ALL_TYPES = ATTACH_IMAGE_TYPES + ATTACH_DOC_TYPES

DEFAULT_TEKS_DESCRIPTIONS_EN = {
    "3.6A": "Classify and sort two- and three-dimensional figures, including cones, cylinders, spheres, triangular and rectangular prisms, and cubes, based on attributes using formal geometric language.",
    "3.6C": "Determine the area of rectangles with whole number side lengths using multiplication related to rows and columns."
}
DEFAULT_TEKS_DESCRIPTIONS_ES = {
    "3.6A": "Clasificar y ordenar figuras bidimensionales y tridimensionales, incluidas los conos, cilindros, esferas, prismas triangulares y prismas rectangulares, y cubos, según atributos usando lenguaje geométrico formal.",
    "3.6C": "Determinar el área de rectángulos con longitudes de lado en números enteros usando multiplicación relacionada con filas y columnas."
}

def _safe_rerun():
    try: st.rerun()
    except Exception: st.experimental_rerun()

def _ts(): return datetime.now().strftime("%Y%m%d_%H%M%S")

def _grade_number_from_label(label: str) -> str:
    return re.sub(r"(st|nd|rd|th)$", "", label.strip())

def filter_teks_by_grade(df: pd.DataFrame, grade_label: str) -> pd.DataFrame:
    if df is None or df.empty or "code" not in df.columns:
        return pd.DataFrame(columns=["code","description_en","description_es"])
    target = _grade_number_from_label(grade_label)
    return df[df["code"].astype(str).str.startswith(f"{target}.", na=False)].copy()

def render_teks_template_download():
    tmpl = io.StringIO()
    tmpl.write("subject,grade,code,description_en,description_es,strand,type\n")
    tmpl.write('math,3,3.6A,"Classify and sort two- and three-dimensional figures …","Clasificar y ordenar figuras bidimensionales y tridimensionales …",Geometry,readiness\n')
    tmpl.write('math,3,3.6C,"Determine the area of rectangles with whole number side lengths…","Determinar el área de rectángulos con longitudes de lado en números enteros…",Geometry,readiness\n')
    st.download_button("Download TEKS CSV template", data=tmpl.getvalue().encode("utf-8"),
                       file_name="teks_template.csv", mime="text/csv")

def write_pdf_from_markdown(md_text: str, out_path: Path, title_override: str|None=None):
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="H1", parent=styles["Heading1"], spaceAfter=12))
    styles.add(ParagraphStyle(name="H2", parent=styles["Heading2"], spaceAfter=8))
    styles.add(ParagraphStyle(name="Body", parent=styles["BodyText"], leading=14))
    doc = SimpleDocTemplate(str(out_path), pagesize=letter,
                            leftMargin=0.75*inch, rightMargin=0.75*inch,
                            topMargin=0.75*inch, bottomMargin=0.75*inch)
    story = []
    text = md_text
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.startswith("# ") and title_override:
            story.append(Paragraph(f"<b>{title_override}</b>", styles["H1"]))
        elif line.startswith("# "):
            story.append(Paragraph(f"<b>{line[2:].strip()}</b>", styles["H1"]))
        elif line.startswith("## "):
            story.append(Paragraph(f"<b>{line[3:].strip()}</b>", styles["H2"]))
        elif line.startswith("**") and line.endswith("**") and len(line)>4:
            story.append(Paragraph(f"<b>{line.strip('**')}</b>", styles["Body"]))
        elif line.startswith(("- ","• ")):
            story.append(Paragraph(line[2:], styles["Body"]))
        else:
            story.append(Paragraph(line, styles["Body"]))
        story.append(Spacer(1, 6))
    doc.build(story)

def render_download_and_preview(label: str, pdf_path: Path, preview_label: str, expanded: bool=False):
    st.success(f"{label} ready → {pdf_path.name}")
    try:
        if HAS_PDF_VIEWER:
            with st.expander(preview_label, expanded=expanded):
                pdf_viewer(str(pdf_path))
    except Exception:
        pass
    with open(pdf_path, "rb") as f:
        st.download_button(f"Download {label} PDF", f, file_name=pdf_path.name, mime="application/pdf",
                           key=f"dl_{label.replace(' ','_').lower()}_{pdf_path.name}")

def zip_bundle(files: list[Path], out_zip: Path):
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            if p and p.exists(): zf.write(p, arcname=p.name)

# ---- attachments extraction (unchanged) ----
def _extract_text_from_csv(bytes_data: bytes, max_chars: int = 1200) -> str:
    try:
        import pandas as pd
        from io import BytesIO
        df = pd.read_csv(BytesIO(bytes_data))
        return df.head(10).to_csv(index=False)[:max_chars]
    except Exception:
        try: return bytes_data.decode("utf-8", errors="ignore")[:max_chars]
        except Exception: return ""

def _extract_text_from_pdf(bytes_data: bytes, max_pages: int=3, max_chars: int=2000) -> str:
    if not HAS_PYPDF2: return "(PDF attached; text extraction unavailable. Install PyPDF2.)"
    try:
        from io import BytesIO
        reader = PdfReader(BytesIO(bytes_data))
        out = []
        for page in reader.pages[:max_pages]:
            try: out.append(page.extract_text() or "")
            except Exception: continue
        txt = "\n".join(out).strip()
        return txt[:max_chars] if txt else "(PDF attached; no extractable text.)"
    except Exception:
        return "(PDF attached; failed to extract text.)"

def build_attachments_summary(uploaded_files: list):
    lines, previews = [], []
    if not uploaded_files: return "", previews
    for uf in uploaded_files:
        name = uf.name; ext = name.split(".")[-1].lower() if "." in name else ""
        data = uf.getvalue()
        if ext in ("txt","md"):
            text = data.decode("utf-8", errors="ignore"); lines.append(f"- TEXT {name}: {text.strip()[:1200]}"); previews.append(f"{name} (text)")
        elif ext == "csv":
            excerpt = _extract_text_from_csv(data); lines.append(f"- CSV {name} (excerpt):\n{excerpt}"); previews.append(f"{name} (csv)")
        elif ext == "pdf":
            excerpt = _extract_text_from_pdf(data); lines.append(f"- PDF {name} (excerpt):\n{excerpt}"); previews.append(f"{name} (pdf)")
        elif ext in ATTACH_IMAGE_TYPES:
            lines.append(f"- IMAGE {name}: (no OCR)"); previews.append(f"{name} (image)")
        else:
            lines.append(f"- FILE {name}: ({len(data)} bytes)"); previews.append(f"{name} (file)")
    return ("ATTACHMENTS SUMMARY\n" + "\n".join(lines)), previews

# ---------------- UI ----------------
st.set_page_config(page_title="STAAR AI Generator", layout="wide")
st.markdown("""<style>textarea[aria-label="Teacher Notes"] { width: 100% !important; }</style>""", unsafe_allow_html=True)

with st.sidebar:
    st.subheader("Configuration")
    st.markdown("**Upload TEKS CSV** (supports `code`, `description_en`, `description_es`, or `description`)")
    teks_file = st.file_uploader("Upload TEKS CSV", type=["csv"])
    if teks_file:
        try:
            raw_df = pd.read_csv(teks_file)
            raw_df.columns = [c.strip().lower() for c in raw_df.columns]
            code_col = next((c for c in ["code","teks","teks_code","standard","standard_code","id"] if c in raw_df.columns), None) or raw_df.columns[0]
            desc_en_col = next((c for c in ["description_en","desc_en","english","english_description","en_description","description (en)","descriptionen"] if c in raw_df.columns), None)
            desc_es_col = next((c for c in ["description_es","desc_es","spanish","spanish_description","es_description","descripcion","descripcion_es","descripción","descripción_es","description (es)","descripciones"] if c in raw_df.columns), None)
            generic_desc_col = next((c for c in ["description","desc","teks_description","standard_description","student_expectation","se","text","statement","learning_objective"] if c in raw_df.columns), None)

            df = raw_df.rename(columns={code_col:"code"})
            if desc_en_col: df = df.rename(columns={desc_en_col:"description_en"})
            else: df["description_en"] = ""
            if desc_es_col: df = df.rename(columns={desc_es_col:"description_es"})
            else: df["description_es"] = ""
            if not desc_en_col and generic_desc_col:
                df = df.rename(columns={generic_desc_col:"description"})
                df["description_en"] = df["description"].astype(str)
            elif "description" in df.columns and df["description_en"].eq("").all():
                df["description_en"] = df["description"].astype(str)

            teks_df = df[["code","description_en","description_es"]].copy()
            st.session_state["teks_df"] = teks_df
            st.success(f"Loaded {len(teks_df)} TEKS rows (code='{code_col}', EN='{desc_en_col or '—'}', ES='{desc_es_col or '—'}'{', generic='+generic_desc_col if generic_desc_col else ''}).")
        except Exception as e:
            st.error(f"Failed to read CSV: {e}")
    teks_df = st.session_state.get("teks_df")

    bilingual = st.toggle("Bilingual (EN/ES)", value=True)
    strict_align = st.toggle("Strict TEKS Alignment (verify + revise)", value=True)
    st.subheader("Question Types")
    question_types = st.multiselect("Preferred types (AI aims for these)", options=QUESTION_TYPE_OPTIONS, default=["Short Answer"])

    st.markdown("---")
    with st.expander("Diagnostics", expanded=False):
        if OPENAI_KEY: st.success("OPENAI_API_KEY found ✅")
        else: st.warning("OPENAI_API_KEY not found.")
        if HAS_LLMCLIENT and OPENAI_KEY:
            if st.button("Quick LLM ping"):
                try:
                    LLMClient().complete("READY", max_tokens=2, temperature=0.0)
                    st.success("LLM responded. ✅")
                except Exception as e:
                    st.error(f"Ping failed: {e}")
        else:
            st.caption("LLM ping disabled (content_llm.py or key missing).")

st.title(APP_TITLE)

colA, colB, colC = st.columns([1,2,2])
with colA:
    grade_label = st.selectbox("Grade", GRADE_OPTIONS, index=2, key="grade_widget")
with colB:
    show_all_teks = st.toggle("Show all TEKS (ignore grade filter)", value=False, key="show_all_teks_widget")
with colC:
    pass

choices = []
def _grade_number_from_label(label: str) -> str:
    return re.sub(r"(st|nd|rd|th)$", "", label.strip())

def filter_teks_by_grade(df: pd.DataFrame, grade_label: str) -> pd.DataFrame:
    if df is None or df.empty or "code" not in df.columns:
        return pd.DataFrame(columns=["code","description_en","description_es"])
    target = _grade_number_from_label(grade_label)
    return df[df["code"].astype(str).str.startswith(f"{target}.", na=False)].copy()

if teks_df is not None and not teks_df.empty:
    df_g = teks_df.copy() if show_all_teks else filter_teks_by_grade(teks_df, grade_label)
    if not df_g.empty:
        def fmt_row(row):
            code = str(row.get("code","")).strip()
            en = str(row.get("description_en","") or "").strip()
            return f"{code} — {en}" if en else code
        choices = [fmt_row(r) for _, r in df_g.iterrows()]

col1, col2 = st.columns([3,2])
with col1:
    teks_select = st.selectbox("TEKS (filtered by grade)",
                               options=["(type manually)"] + choices if choices else ["(type manually)"],
                               index=0, key="teks_select_widget")
with col2:
    teks_manual = st.text_input("TEKS (manual override)", key="teks_manual_widget")

def _lookup_descs(df: pd.DataFrame|None, code: str) -> tuple[str,str]:
    if df is None or not code: return "",""
    try:
        sub = df[df["code"].astype(str).str.strip() == code]
        if not sub.empty:
            en = str(sub.iloc[0].get("description_en","") or "")
            es = str(sub.iloc[0].get("description_es","") or "")
            return en, es
    except Exception: pass
    return "",""

teks_code = teks_manual.strip() if teks_manual.strip() else (teks_select.split(" — ",1)[0].strip() if teks_select and teks_select!="(type manually)" else "")
desc_en, desc_es = _lookup_descs(teks_df, teks_code)
if not desc_en: desc_en = DEFAULT_TEKS_DESCRIPTIONS_EN.get(teks_code, "")
if not desc_es: desc_es = DEFAULT_TEKS_DESCRIPTIONS_ES.get(teks_code, "")

if teks_code and (not desc_en):
    st.warning(f"No description found for **{teks_code}**. Paste the official TEKS description (EN) below for best results.")
    desc_en = st.text_area("TEKS description (EN)", key="teks_desc_manual_en", placeholder="Paste the official TEKS statement…")

btn1, btn2, btn3, btn4 = st.columns([1,1,1,1])
with btn1: gen_lesson_btn = st.button("Generate Lesson Plan", type="primary")
with btn2: gen_worksheet_btn = st.button("Generate Worksheet", type="secondary")
with btn3: gen_both_btn = st.button("Generate Both", type="secondary")
with btn4: reset_btn = st.button("Reset")

st.markdown("#### Teacher Notes & Attachments")
teacher_notes = st.text_area("Teacher Notes", key="teacher_notes", height=180)
attachments = st.file_uploader("Attach files/images (optional)", type=ATTACH_ALL_TYPES,
                               accept_multiple_files=True, key="notes_attachments")
def _extract_summary(uploaded):
    return build_attachments_summary(uploaded or [])
attach_summary, attach_preview = _extract_summary(attachments)
if attachments:
    cols = st.columns(min(4, len(attachments)))
    for i, f in enumerate(attachments):
        ext = f.name.split(".")[-1].lower() if "." in f.name else ""
        with cols[i % len(cols)]:
            if ext in ATTACH_IMAGE_TYPES: st.image(f, caption=f.name, use_container_width=True)
            else: st.caption(attach_preview[i])
teacher_notes_aug = (teacher_notes + ("\n\n" + attach_summary if attach_summary else "")).strip()

if reset_btn:
    for k in ["grade_widget","show_all_teks_widget","teks_select_widget","teks_manual_widget",
              "teacher_notes","notes_attachments","teks_desc_manual_en",
              "last_answer_key_pdf"]:
        if k in st.session_state: del st.session_state[k]
    last_zip = st.session_state.get("last_zip_path")
    if last_zip:
        try: Path(last_zip).unlink(missing_ok=True)
        except Exception: pass
        del st.session_state["last_zip_path"]
    for k in ["last_lesson_pdf","last_worksheet_pdf"]: st.session_state.pop(k, None)
    _safe_rerun()

tabs = st.tabs(["Generate & Preview", "Bundle ZIP"])
last_lesson_pdf = st.session_state.get("last_lesson_pdf")
last_worksheet_pdf = st.session_state.get("last_worksheet_pdf")
last_answer_key_pdf = st.session_state.get("last_answer_key_pdf")

def _require_ai_ready():
    if not OPENAI_KEY:
        st.error("AI-only mode: set OPENAI_API_KEY.")
        return False
    if not HAS_LLMCLIENT:
        st.error("AI-only mode: content_llm.LLMClient not found.")
        return False
    return True

def _desc_for_prompt(en: str, es: str, bilingual: bool) -> str:
    en = (en or "").strip(); es = (es or "").strip()
    if bilingual and es: return f"EN: {en}\nES: {es}"
    return en

def _gen_and_show(doc_type: str):
    if not _require_ai_ready(): return None
    if not teks_code: st.error("Select or type a TEKS code."); return None
    if not desc_en: st.error("Provide the TEKS description (EN) from the CSV or paste it."); return None
    teks_desc = _desc_for_prompt(desc_en, desc_es, bilingual)

    try:
        if doc_type == "lesson":
            st.info("Generating lesson plan (TEKS-aligned)…")
            md = gen.generate_lesson_plan_ai(
                grade_label=grade_label, teks_code=teks_code, teks_description=teks_desc,
                bilingual=bilingual, teacher_notes=teacher_notes_aug, strict_align=strict_align
            )
        else:
            st.info("Generating worksheet (TEKS-aligned)…")
            result = gen.generate_worksheet_ai(
                grade_label=grade_label, teks_code=teks_code, teks_description=teks_desc,
                question_types=question_types, bilingual=bilingual, teacher_notes=teacher_notes_aug,
                strict_align=strict_align
            )
    except Exception as e:
        st.error(f"{doc_type.title()} generation failed: {e}")
        return None

    if doc_type == "lesson":
        out_name = f"Lesson_{grade_label.replace(' ','')}_{teks_code.replace('.','_')}_{_ts()}.pdf"
        out_path = OUTPUT_DIR / out_name
        try:
            write_pdf_from_markdown(md, out_path)
            st.session_state["last_lesson_pdf"] = str(out_path)
            render_download_and_preview("Lesson Plan", out_path, "Preview: Lesson", expanded=True)
            return out_path
        except Exception as e:
            st.error(f"Failed to build PDF: {e}")
            return None
    else:
        # Worksheet: separate Answer Key
        if isinstance(result, dict):
            ws_md = result.get("worksheet_md","")
            key_md = result.get("answer_key_md","")
        else:
            ws_md = str(result); key_md = ""

        ws_name = f"Worksheet_{grade_label.replace(' ','')}_{teks_code.replace('.','_')}_{_ts()}.pdf"
        ws_path = OUTPUT_DIR / ws_name
        try:
            write_pdf_from_markdown(ws_md, ws_path)
            st.session_state["last_worksheet_pdf"] = str(ws_path)
            render_download_and_preview("Worksheet", ws_path, "Preview: Worksheet", expanded=True)
        except Exception as e:
            st.error(f"Failed to build Worksheet PDF: {e}")
            return None

        if key_md.strip():
            key_name = f"AnswerKey_{grade_label.replace(' ','')}_{teks_code.replace('.','_')}_{_ts()}.pdf"
            key_path = OUTPUT_DIR / key_name
            try:
                write_pdf_from_markdown(key_md, key_path, title_override=f"Answer Key — Math {grade_label} — {teks_code}")
                st.session_state["last_answer_key_pdf"] = str(key_path)
                with open(key_path, "rb") as f:
                    st.download_button("Download Answer Key PDF", f, file_name=key_name, mime="application/pdf")
            except Exception as e:
                st.warning(f"Could not build Answer Key PDF: {e}")
        else:
            st.info("No separate Answer Key was produced for this worksheet type.")
        return ws_path

with tabs[0]:
    if gen_lesson_btn: _gen_and_show("lesson")
    if gen_worksheet_btn: _gen_and_show("worksheet")
    if gen_both_btn:
        lp = _gen_and_show("lesson"); wp = _gen_and_show("worksheet")
        if lp and wp: st.success("Generated both.")

with tabs[1]:
    st.markdown("### Bundle ZIP")
    lp = Path(last_lesson_pdf) if last_lesson_pdf else None
    wp = Path(last_worksheet_pdf) if last_worksheet_pdf else None
    ak = Path(last_answer_key_pdf) if last_answer_key_pdf else None
    st.text(f"Lesson: {lp.name if lp else '(none)'}")
    st.text(f"Worksheet: {wp.name if wp else '(none)'}")
    st.text(f"Answer Key: {ak.name if ak else '(none)'}")
    if st.button("Create ZIP"):
        if not any([lp and lp.exists(), wp and wp.exists(), ak and ak.exists()]):
            st.error("Generate at least one PDF first.")
        else:
            zip_path = OUTPUT_DIR / f"Bundle_{grade_label.replace(' ','')}_{teks_code.replace('.','_')}_{_ts()}.zip"
            try:
                zip_bundle([p for p in [lp, wp, ak] if p], zip_path)
                st.session_state["last_zip_path"] = str(zip_path)
                st.success(f"ZIP ready → {zip_path.name}")
                with open(zip_path, "rb") as f:
                    st.download_button("Download ZIP", f, file_name=zip_path.name, mime="application/zip")
            except Exception as e:
                st.error(f"Failed to create ZIP: {e}")

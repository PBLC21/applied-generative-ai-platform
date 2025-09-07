# app.py — TEKS AI — Lesson & Worksheet Generator
# - Title lower, centered
# - Announcements a bit wider
# - Teacher Notes file upload (PDF/DOCX/TXT/IMG)
# - Strong text extraction from uploads (PyMuPDF + OCR fallback)
# - Inline PREVIEW via PyMuPDF image render (page slider / show-all)
# - Robust Generate handler with clear validation & errors

from __future__ import annotations
import os, io, csv, base64, requests
from typing import List, Dict, Optional, Tuple
from dataclasses import is_dataclass, asdict
import streamlit as st

# Optional: load .env for OPENAI_* locally
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

st.set_page_config(page_title="TEKS AI — Lesson & Worksheet Generator", layout="wide")

TITLE_AND_STYLES = """
<style>
:root{
  --muted:#64748b;--border:#e2e8f0;--soft:#f8fafc;--accent:#f43f5e;--accent2:#f97316;
}
.block-container{padding-top:2.2rem;} /* LOWER the title */
.app-header{display:flex;justify-content:center;align-items:center;text-align:center;margin:.2rem 0 1rem;}
.app-header h1{
  font-size:2.0rem;line-height:1.25;margin:0;max-width:1100px;
  word-break:break-word;overflow-wrap:anywhere;white-space:normal;
}
/* Announcement cards */
.ann-card{border-radius:16px;padding:12px 14px;background:linear-gradient(135deg,#fff7ed,#ffffff);
  border:1px solid #fde68a;box-shadow:0 2px 10px rgba(0,0,0,.05);}
.ann-card + .ann-card{margin-top:12px;}
.ann-title{font-weight:700;font-size:1rem;margin-bottom:4px;}
.ann-sub{color:#475569;font-size:.9rem;line-height:1.35;}
.badge{display:inline-block;margin-left:8px;padding:2px 8px;border-radius:999px;font-size:.72rem;color:#fff;
  background:linear-gradient(to right,var(--accent),var(--accent2));}
.small-note{color:#777;font-size:.8rem;}
hr.soft{border:none;border-top:1px solid rgba(148,163,184,.25);margin:.4rem 0 .6rem 0;}
/* TEKS summary card */
.teks-summary{background:var(--soft);border:1px solid var(--border);border-radius:12px;padding:10px 12px;margin-top:.25rem;}
.teks-summary .label{color:#0f172a;font-weight:700;}
.teks-summary .meta{color:#334155;}
</style>
<div class="app-header"><h1>TEKS AI — Lesson &amp; Worksheet Generator</h1></div>
"""
st.markdown(TITLE_AND_STYLES, unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
DEFAULT_TEKS_URL = ("https://docs.google.com/spreadsheets/d/"
    "1S_bzLiL5wQiwKuY5fzaIX8v0uuYT3iupdjYHMVjXEWk/export?format=csv&gid=1513594026")
GRADE_OPTIONS = ["Kinder","1","2","3","4","5","6"]
SUBJECT_OPTIONS = ["Math","Reading"]

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _normalize_grade_in(g:str)->str:
    g=(g or "").strip()
    if g.lower() in {"k","kinder","kindergarten"}: return "K"
    m={"1":"1","1st":"1","grade 1":"1","2":"2","2nd":"2","grade 2":"2","3":"3","3rd":"3","grade 3":"3",
       "4":"4","4th":"4","grade 4":"4","5":"5","5th":"5","grade 5":"5","6":"6","6th":"6","grade 6":"6"}
    return m.get(g.lower(), g)

def _normalize_subject_in(s:str)->str:
    s=(s or "").strip().title()
    if s.startswith("Math"): return "Math"
    if s.startswith("Read"): return "Reading"
    return s

def _smart_decode(b:bytes)->str:
    try:
        s=b.decode("utf-8-sig")
        if any(x in s for x in ("Ã¡","Ã©","Ã­","Ã³","Ãº","Ã±","ÃÁ","Ã‰","Ã“","Ãš","Ã‘")):
            try: return s.encode("latin1").decode("utf-8")
            except Exception: return s
        return s
    except Exception:
        try: return b.decode("latin1")
        except Exception: return b.decode("utf-8", errors="ignore")

def _read_csv_bytes(content:bytes)->List[Dict[str,str]]:
    import io, csv as _csv
    decoded=_smart_decode(content)
    reader=_csv.DictReader(io.StringIO(decoded))
    rows=[]
    for r in reader:
        row={(k or "").strip().lower():(v or "").strip() for k,v in r.items()}
        grade=_normalize_grade_in(row.get("grade",""))
        subject=_normalize_subject_in(row.get("subject",""))
        code=row.get("code","")
        if grade and subject and code:
            rows.append({
                "grade":grade,"subject":subject,"code":code,
                "strand":row.get("strand",""),
                "description_en":row.get("description_en","") or row.get("description",""),
                "description_es":row.get("description_es",""),
                "type":row.get("type",""),
            })
    return rows

@st.cache_data(show_spinner=False, ttl=24*3600)
def _embedded_sample()->List[Dict[str,str]]:
    return [
        {"subject":"Math","grade":"2","code":"2.6A","strand":"Foundations of Multiplication",
         "description_en":"Model and describe contextual multiplication situations.",
         "description_es":"Modela y describe situaciones de multiplicación en contexto.","type":"Readiness"},
        {"subject":"Reading","grade":"2","code":"2.6A","strand":"Comprehension",
         "description_en":"Establish a purpose for reading and monitor comprehension.",
         "description_es":"Establece un propósito para la lectura y monitorea la comprensión.","type":"Readiness"},
    ]

@st.cache_data(show_spinner=False, ttl=24*3600)
def load_teks_catalog()->Tuple[List[Dict[str,str]], str]:
    try: secrets_url=st.secrets.get("TEKS_SOURCE_URL", None)  # type: ignore[attr-defined]
    except Exception: secrets_url=None
    url=(secrets_url or os.environ.get("TEKS_SOURCE_URL") or DEFAULT_TEKS_URL).strip()
    try:
        r=requests.get(url, timeout=12); r.raise_for_status()
        rows=_read_csv_bytes(r.content)
        if rows: return rows, "Remote (Google Sheet CSV)"
    except Exception:
        pass
    return _embedded_sample(), "Embedded SAMPLE (fallback)"

def filter_teks(catalog:List[Dict[str,str]], grade_display:Optional[str], subject_display:Optional[str], query:str="")->List[Dict[str,str]]:
    g="K" if (grade_display or "")=="Kinder" else _normalize_grade_in(grade_display or "")
    s=_normalize_subject_in(subject_display or ""); q=(query or "").lower().strip()
    out=[]
    for row in catalog:
        if g and row["grade"]!=g: continue
        if s and row["subject"]!=s: continue
        if q:
            hay=" ".join([row["code"],row.get("strand",""),row.get("description_en",""),row.get("description_es","")]).lower()
            if q not in hay: continue
        out.append(row)
    out.sort(key=lambda r:(r["grade"],r["subject"],r["code"]))
    return out

# -----------------------------------------------------------------------------
# Layout (Announcements a bit wider)
# -----------------------------------------------------------------------------
left, right = st.columns([0.36, 2.64], vertical_alignment="top")

with left:
    st.markdown("### 📣 Announcements")
    st.markdown(
        """
        <div class="ann-card">
          <div class="ann-title">🤖 AI Agent coming soon <span class="badge">Preview</span></div>
          <div class="ann-sub">Autonomous lesson refinement, TEKS validation, and worksheet QA—powered by your catalog + Teacher Notes.</div>
        </div>
        <div class="ann-card">
          <div class="ann-title">🚀 Are you ready for the challenge?</div>
          <div class="ann-sub">Join our 1-month pilot to stress-test real classroom flows and polish v1.0.</div>
          <hr class="soft"/>
          <div class="small-note">Tip: toggle <b>Bilingual Output</b> and try Reading <code>2.6A</code> to preview EN/ES generation.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with right:
    st.markdown("### Grade & TEKS")
    col_g, col_s = st.columns([1,1], vertical_alignment="center")
    with col_g:
        grade_choice = st.selectbox("Grade", GRADE_OPTIONS, index=None, placeholder="Select a grade...", key="grade_choice")
    with col_s:
        subject_choice = st.selectbox("Subject", SUBJECT_OPTIONS, index=None, placeholder="Select a subject...", key="subject_choice")

    catalog, source_label = load_teks_catalog()
    disabled = not (grade_choice and subject_choice)

    filter_text = st.text_input("Filter TEKS by code, strand, or keyword (EN/ES)", value="", disabled=disabled,
                                placeholder="e.g., 2.6A, multiplication, 'inferences', 'multiplicación'...", key="teks_filter_text")

    options=[]; label_map={}
    if not disabled:
        for row in filter_teks(catalog, grade_choice, subject_choice, filter_text):
            strand=(row.get("strand") or "").strip()
            snippet=(row.get("description_en") or row.get("description_es") or "")[:70]
            label=f"{row['code']} — {strand if strand else snippet}"
            label_map[label]=row; options.append(label)

    teks_label = st.selectbox("TEKS (auto-populated by grade & subject)", options=options, index=None,
                              disabled=disabled or not options, placeholder="Pick a TEKS code...", key="teks_dropdown")

    manual_code = st.text_input("Or enter TEKS code manually", value="", placeholder="e.g., 2.6A",
                                help="Manual entry overrides the dropdown if filled.", key="manual_teks_code")

    selected_meta = label_map.get(teks_label) if teks_label else None
    if manual_code.strip():
        teks_code = manual_code.strip()
        if selected_meta and selected_meta["code"].lower()!=teks_code.lower(): selected_meta=None
    else:
        teks_code = selected_meta["code"] if selected_meta else None

    if selected_meta:
        en=selected_meta.get("description_en",""); es=selected_meta.get("description_es","")
        strand=selected_meta.get("strand",""); typ=selected_meta.get("type","")
        st.markdown(
            f"""
            <div class="teks-summary">
              <div class="label">{selected_meta['code']}</div>
              <div class="meta">{strand}{' · ' + typ if typ else ''}</div>
              {"<div style='margin-top:8px'><b>English:</b><br/>"+en+"</div>" if en else ""}
              {"<div style='margin-top:8px'><b>Español:</b><br/>"+es+"</div>" if es else ""}
            </div>""",
            unsafe_allow_html=True,
        )

    # Health + Reload + AI status
    ai_on = bool(os.getenv("OPENAI_API_KEY")); model = os.getenv("OPENAI_MODEL","gpt-4o-mini")
    st.caption(f"TEKS source: {source_label} — {len(catalog)} rows · AI: {'ON ✅' if ai_on else 'OFF ⛔️'} · Model: {model}")
    rc1, _ = st.columns([1,6])
    with rc1:
        if st.button("Reload TEKS catalog", help="Clear cache and re-fetch from the source URL."):
            load_teks_catalog.clear(); st.success("TEKS reloaded from source."); st.rerun()

    # Controls
    c1,c2,c3 = st.columns([1,1,1])
    with c1:
        bilingual = st.toggle("Bilingual Output (EN+ES)", value=True, help="Generate materials in both English and Spanish.", key="bilingual_toggle")
    with c2:
        generate = st.button("Generate Lesson Plan & Worksheets", type="primary", use_container_width=True, key="btn_generate")
    with c3:
        if st.button("Reset Form", use_container_width=True, key="btn_reset"):
            for k in ["grade_choice","subject_choice","teks_filter_text","teks_dropdown","manual_teks_code","bilingual_toggle","teacher_notes","uploaded_notes"]:
                if k in st.session_state: del st.session_state[k]
            st.rerun()

    # ─────────────────── Teacher Notes + File Upload ────────────────────
    teacher_notes = st.text_area(
        "Teacher Notes (optional)", height=120,
        placeholder="Scaffolds, language supports, manipulatives, accommodations, student interests, etc.",
        key="teacher_notes",
    )
    uploaded = st.file_uploader(
        "Attach reference files (optional): PDF, DOCX, TXT, PNG, JPG",
        type=["pdf","docx","txt","png","jpg","jpeg"],
        accept_multiple_files=True,
        help="Attached content will be summarized into the AI prompt.",
        key="uploaded_notes",
    )

    # Strong extractor (PyMuPDF + OCR fallback). Returns combined text and per-file details.
    def _extract_text_from_uploads(files) -> Tuple[str, List[Tuple[str,int,str]]]:
        if not files: return "", []
        combined = []
        details: List[Tuple[str,int,str]] = []
        PER_FILE_CAP = 8000
        OVERALL_CAP = 20000

        for f in files:
            name = f.name
            lname = name.lower()
            note = ""
            text = ""

            try:
                if lname.endswith(".pdf"):
                    try:
                        import fitz  # PyMuPDF
                        b = f.read()
                        doc = fitz.open(stream=b, filetype="pdf")
                        chunks = []
                        for page in doc:
                            t = page.get_text("text") or ""
                            if not t.strip():
                                # OCR fallback for scanned page
                                try:
                                    import pytesseract
                                    from PIL import Image
                                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                                    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                                    t = pytesseract.image_to_string(img)
                                    if not note: note = "OCR used"
                                except Exception:
                                    pass
                            if t: chunks.append(t)
                        text = "\n".join(chunks)
                        if not text.strip():
                            note = note or "no text found"
                    except Exception:
                        note = "PyMuPDF failed"; text = ""

                elif lname.endswith(".docx"):
                    try:
                        import docx
                        d = docx.Document(f)
                        text = "\n".join(p.text for p in d.paragraphs)
                    except Exception:
                        note = "docx read failed"; text = ""

                elif lname.endswith(".txt"):
                    try:
                        text = f.read().decode("utf-8", "ignore")
                    except Exception:
                        note = "txt decode failed"; text = ""

                elif lname.endswith((".png",".jpg",".jpeg")):
                    try:
                        import pytesseract
                        from PIL import Image
                        f.seek(0)
                        img = Image.open(f).convert("RGB")
                        text = pytesseract.image_to_string(img)
                        if not text.strip(): note = "image OCR produced no text"
                    except Exception:
                        note = "image OCR failed"; text = ""

                text = (text or "")[:PER_FILE_CAP]

            except Exception:
                note = note or "read error"; text = ""

            details.append((name, len(text), note))
            if text:
                combined.append(f"[FILE: {name}]{' ('+note+')' if note else ''}\n{text}\n")

            if sum(len(x) for x in combined) >= OVERALL_CAP:
                break

        return "\n\n".join(combined)[:OVERALL_CAP], details

    uploads_text, upload_details = _extract_text_from_uploads(uploaded)
    if uploads_text:
        st.caption(f"Attached notes processed ({len(uploads_text)} chars).")
        with st.expander("Preview what was read from your uploads"):
            for fname, nchar, note in upload_details:
                st.write(f"**{fname}** — {nchar} chars" + (f" · _{note}_" if note else ""))
            st.code(uploads_text[:1200] + ("… (truncated)" if len(uploads_text) > 1200 else ""))
    else:
        if uploaded:
            st.warning("Could not extract any text from the uploaded files. If they are scans, OCR may be needed.")

    # ───────────────────── Generator call wrapper ────────────────────────
    def _safe_call_generator(teks_code, grade_choice, subject_choice, bilingual, teacher_notes, attachments_text, meta):
        import inspect
        try:
            from generator import generate_all_outputs
        except Exception as e:
            st.error("Could not import generator.generate_all_outputs"); st.exception(e); return None
        norm_grade="K" if grade_choice=="Kinder" else grade_choice
        desired = {
            "teks_code":teks_code,"grade":norm_grade,"subject":subject_choice,
            "bilingual":bilingual,
            "teacher_notes": (teacher_notes or ""),
            "attachments_text": (attachments_text or ""),
            "teks_description_en": meta.get("description_en") if meta else None,
            "teks_description_es": meta.get("description_es") if meta else None,
            "teks_strand": meta.get("strand") if meta else None,
            "teks_type":  meta.get("type") if meta else None,
        }
        sig=inspect.signature(generate_all_outputs)
        kwargs={k:v for k,v in desired.items() if k in sig.parameters and v is not None}
        try: return generate_all_outputs(**kwargs)
        except Exception as e:
            st.error("The generator raised an exception."); st.exception(e); return None

# ───────────────── PDF preview (slider / show-all) ───────────────────────────
def _preview_pdf(path: str, label: str):
    import os, base64
    colA, colB = st.columns([4, 1])

    try:
        import fitz  # PyMuPDF
        doc = fitz.open(path)
        num_pages = len(doc)

        with colA:
            st.caption(f"{label} • {num_pages} page(s)")
            show_all = st.toggle(
                "Show all pages", value=False,
                help="Render every page below (can be slower for long PDFs).",
                key=f"showall_{label}"
            )
            if not show_all:
                page_index = st.slider("Page", 1, num_pages, 1, key=f"slider_{label}")
                page = doc.load_page(page_index - 1)
                pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5))  # crisp preview
                st.image(pix.tobytes("png"), use_container_width=True,
                         caption=f"{label} — Page {page_index}")
            else:
                for i in range(num_pages):
                    page = doc.load_page(i)
                    pix = page.get_pixmap(matrix=fitz.Matrix(2.2, 2.2))
                    st.image(pix.tobytes("png"), use_container_width=True,
                             caption=f"{label} — Page {i+1}")

        with colB:
            st.download_button(
                f"Download {label}", open(path, "rb"),
                file_name=os.path.basename(path),
                mime="application/pdf", use_container_width=True
            )

    except Exception:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        with colA:
            st.markdown(
                f"PDF inline preview unavailable. "
                f"[Open in a new tab](data:application/pdf;base64,{b64})."
            )
        with colB:
            st.download_button(
                f"Download {label}", open(path, "rb"),
                file_name=os.path.basename(path),
                mime="application/pdf", use_container_width=True
            )

# ───────────────── Robust generate handler ───────────────────────────────────
if generate:
    st.toast("Starting generation…", icon="⏳")

    try:
        if not (grade_choice and subject_choice):
            st.error("Select a **Grade** and **Subject** first.")
            st.stop()

        _code = None
        if selected_meta:
            _code = selected_meta.get("code")
        if manual_code.strip():
            _code = manual_code.strip()  # manual overrides

        if not _code:
            st.error("Pick a TEKS in the dropdown **or** type one manually (e.g., `3.2D`).")
            st.stop()

        with st.spinner(f"Generating materials for TEKS {_code}…"):
            outputs = _safe_call_generator(
                _code, grade_choice or "", subject_choice or "",
                bilingual, teacher_notes, uploads_text, selected_meta
            )

        if not outputs:
            st.error("Generation failed. See the error above (if any).")
            st.stop()

        st.success("Done! Your files are ready below.")
        if is_dataclass(outputs):
            outputs = asdict(outputs)

        # Downloads row
        cdl1, cdl2, cdl3, cdl4 = st.columns(4)
        with cdl1:
            st.download_button(
                "Lesson Plan PDF", open(outputs["lesson_plan_fp"], "rb"),
                file_name=os.path.basename(outputs["lesson_plan_fp"]),
                mime="application/pdf", use_container_width=True
            )
        with cdl2:
            st.download_button(
                "Worksheet PDF", open(outputs["worksheet_fp"], "rb"),
                file_name=os.path.basename(outputs["worksheet_fp"]),
                mime="application/pdf", use_container_width=True
            )
        with cdl3:
            st.download_button(
                "Answer Key PDF", open(outputs["answer_key_fp"], "rb"),
                file_name=os.path.basename(outputs["answer_key_fp"]),
                mime="application/pdf", use_container_width=True
            )
        with cdl4:
            st.download_button(
                "All as ZIP", open(outputs["zip_fp"], "rb"),
                file_name=os.path.basename(outputs["zip_fp"]),
                mime="application/zip", use_container_width=True
            )

        # Previews (image-based)
        tabs = st.tabs(["Preview: Lesson Plan", "Preview: Worksheet", "Preview: Answer Key"])
        with tabs[0]:
            _preview_pdf(outputs["lesson_plan_fp"], "Lesson Plan")
        with tabs[1]:
            _preview_pdf(outputs["worksheet_fp"], "Worksheet")
        with tabs[2]:
            _preview_pdf(outputs["answer_key_fp"], "Answer Key")

        st.toast("Generation complete ✅")

    except Exception as e:
        st.error("Unexpected error during generation.")
        st.exception(e)

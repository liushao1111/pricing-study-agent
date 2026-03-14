import os
import re
import subprocess
import tempfile
from pathlib import Path

import streamlit as st
from google import genai
from google.genai import types

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="Pricing Study Agent", page_icon="🎓", layout="wide")

MODEL = "gemini-2.5-flash"

PODCAST_SYSTEM_PROMPT = """You are "The Pricing Professor" — an engaging podcast host who makes complex \
pricing strategies and business cases come alive through storytelling.

Your style:
- Open with a vivid hook: "Picture this: It's 1998, and a small airline is about to change how \
the world buys tickets..."
- Tell the human story BEHIND the pricing strategy — who made the decision, what pressure they were under
- Explain frameworks (price discrimination, value-based pricing, etc.) through narrative, not bullet points
- Use vivid analogies that MBA students will remember on exam day
- Connect the case to a broader principle at the end
- Sound like a brilliant friend explaining over coffee, not a professor lecturing
- End with 3 crisp takeaways framed as memorable insights, not dry summaries

Format as a natural podcast script (5–10 minutes of speaking time). Use paragraph breaks for \
natural pauses. Mark key terms in [BRACKETS] when first introduced."""

QA_SYSTEM_PROMPT = """You are a sharp MBA pricing professor helping a student prepare for exams and \
deepen their understanding. You have access to their case studies and slides.

Your style:
- Answer questions directly, then add depth
- Connect concepts across multiple cases when relevant
- Challenge the student with follow-up questions when appropriate
- Use real-world examples to reinforce concepts
- Flag what's likely to appear on an exam vs. what's deeper context"""


# ── Helpers ───────────────────────────────────────────────────────────────────
def get_client():
    api_key = st.session_state.get("api_key") or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        st.error("Enter your Gemini API key in the sidebar to get started.")
        st.stop()
    return genai.Client(api_key=api_key)


def extract_text(file_bytes: bytes, filename: str):
    """Extract plain text from docx/pptx. Returns None for native types (PDF etc.)."""
    suffix = Path(filename).suffix.lower()
    if suffix in (".docx", ".doc"):
        import io
        from docx import Document
        doc = Document(io.BytesIO(file_bytes))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    if suffix == ".pptx":
        import io
        from pptx import Presentation
        prs = Presentation(io.BytesIO(file_bytes))
        lines = [
            shape.text_frame.text
            for slide in prs.slides
            for shape in slide.shapes
            if shape.has_text_frame
        ]
        return "\n".join(lines)
    return None


def upload_to_gemini(client, file_bytes: bytes, filename: str):
    """Upload a file to Gemini, converting docx/pptx to text first if needed."""
    text = extract_text(file_bytes, filename)
    suffix = ".txt" if text else Path(filename).suffix.lower()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(text.encode() if text else file_bytes)
        tmp_path = tmp.name
    uploaded = client.files.upload(file=tmp_path, config={"display_name": filename})
    Path(tmp_path).unlink(missing_ok=True)
    return uploaded


def speak(text: str):
    """Read text aloud using macOS built-in TTS (non-blocking)."""
    clean = re.sub(r"\*+|_+|#{1,6}\s*|`+|\[|\]", "", text)
    subprocess.Popen(["say", "-v", "Samantha", "-r", "175", clean])


def cleanup_gemini_files(client, files: list):
    for f in files:
        try:
            client.files.delete(name=f.name)
        except Exception:
            pass


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🎓 Pricing Study Agent")
    st.caption("Your MBA pricing class companion")
    st.divider()

    api_key_input = st.text_input(
        "Gemini API Key",
        value=os.environ.get("GEMINI_API_KEY", ""),
        type="password",
        help="Get your key at aistudio.google.com/app/apikey",
    )
    if api_key_input:
        st.session_state.api_key = api_key_input

    st.divider()
    st.markdown("**Supported file types**")
    st.caption("PDF, Word (.docx), PowerPoint (.pptx), Text")
    st.divider()
    speak_aloud = st.toggle("🔊 Read answers aloud", help="Uses macOS built-in TTS (Samantha voice)")


# ── Tabs ──────────────────────────────────────────────────────────────────────
podcast_tab, qa_tab = st.tabs(["🎙️ Podcast Narration", "💬 Study Q&A"])


# ── Podcast Tab ───────────────────────────────────────────────────────────────
with podcast_tab:
    st.subheader("Turn your case studies into a podcast")
    st.caption("Upload one or more files and get an engaging story-style narration.")

    podcast_files = st.file_uploader(
        "Upload case studies or slides",
        type=["pdf", "docx", "doc", "pptx", "txt", "md"],
        accept_multiple_files=True,
        key="podcast_uploader",
    )

    focus = st.text_input(
        "Focus topic (optional)",
        placeholder="e.g. price discrimination, value-based pricing",
    )

    if st.button("🎙️ Generate Podcast", type="primary", disabled=not podcast_files):
        client = get_client()
        gemini_files = []

        with st.status("Uploading files to Gemini...", expanded=True) as status:
            for f in podcast_files:
                st.write(f"⬆️ {f.name}")
                gf = upload_to_gemini(client, f.read(), f.name)
                gemini_files.append((gf, f.name))
            status.update(label="All files uploaded!", state="complete")

        try:
            for gf, name in gemini_files:
                st.markdown(f"---\n### 🎙️ {name}")
                focus_note = f"\n\nPay special attention to: {focus}" if focus else ""
                prompt = f"Narrate this as a podcast episode for an MBA pricing student.{focus_note}"

                full_text = ""
                output_area = st.empty()

                for chunk in client.models.generate_content_stream(
                    model=MODEL,
                    contents=[gf, prompt],
                    config=types.GenerateContentConfig(system_instruction=PODCAST_SYSTEM_PROMPT),
                ):
                    full_text += chunk.text or ""
                    output_area.markdown(full_text)

                col1, col2 = st.columns(2)
                with col1:
                    st.download_button(
                        "💾 Download transcript",
                        data=full_text,
                        file_name=f"podcast_{Path(name).stem}.txt",
                        mime="text/plain",
                        key=f"dl_{name}",
                    )
                with col2:
                    if speak_aloud:
                        if st.button("🔊 Read aloud", key=f"speak_{name}"):
                            speak(full_text)
        finally:
            cleanup_gemini_files(client, [gf for gf, _ in gemini_files])


# ── Q&A Tab ───────────────────────────────────────────────────────────────────
with qa_tab:
    st.subheader("Ask your MBA pricing professor anything")
    st.caption("Upload your materials, then chat to study, quiz yourself, or go deeper on any topic.")

    # Initialize session state
    for key, default in [
        ("qa_messages", []),
        ("qa_gemini_files", []),
        ("qa_chat", None),
        ("qa_ready", False),
    ]:
        if key not in st.session_state:
            st.session_state[key] = default

    if not st.session_state.qa_ready:
        qa_files = st.file_uploader(
            "Upload case studies or slides",
            type=["pdf", "docx", "doc", "pptx", "txt", "md"],
            accept_multiple_files=True,
            key="qa_uploader",
        )

        if st.button("🚀 Start Study Session", type="primary", disabled=not qa_files):
            client = get_client()

            with st.status("Uploading files...", expanded=True) as status:
                gemini_files = []
                for f in qa_files:
                    st.write(f"⬆️ {f.name}")
                    gf = upload_to_gemini(client, f.read(), f.name)
                    gemini_files.append(gf)
                status.update(label="Ready!", state="complete")

            st.session_state.qa_gemini_files = gemini_files
            st.session_state.qa_chat = client.chats.create(
                model=MODEL,
                config=types.GenerateContentConfig(system_instruction=QA_SYSTEM_PROMPT),
            )

            initial_parts = gemini_files + [
                "I've uploaded my MBA pricing case studies and slides. "
                "Please acknowledge what you have and offer 3 good study questions to start."
            ]
            intro = "".join(
                chunk.text or ""
                for chunk in st.session_state.qa_chat.send_message_stream(initial_parts)
            )
            st.session_state.qa_messages = [{"role": "assistant", "content": intro}]
            st.session_state.qa_ready = True
            if speak_aloud:
                speak(intro)
            st.rerun()

    else:
        if st.button("🔄 New Session / Upload Different Files"):
            client = get_client()
            cleanup_gemini_files(client, st.session_state.qa_gemini_files)
            st.session_state.qa_ready = False
            st.session_state.qa_messages = []
            st.session_state.qa_gemini_files = []
            st.session_state.qa_chat = None
            st.rerun()

        # Render chat history
        for msg in st.session_state.qa_messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        # Chat input
        if user_input := st.chat_input("Ask anything about your pricing materials..."):
            st.session_state.qa_messages.append({"role": "user", "content": user_input})
            with st.chat_message("user"):
                st.markdown(user_input)

            with st.chat_message("assistant"):
                response_text = ""
                placeholder = st.empty()
                for chunk in st.session_state.qa_chat.send_message_stream(user_input):
                    response_text += chunk.text or ""
                    placeholder.markdown(response_text)

            st.session_state.qa_messages.append({"role": "assistant", "content": response_text})
            if speak_aloud:
                speak(response_text)

"""Streamlit front end for PDFBot.

Run with:  streamlit run pdf_app.py

All the actual work lives in pdfbot.py — this file only handles the interface.
"""

import os
import tempfile

import streamlit as st
os.environ["GOOGLE_API_KEY"] = st.secrets["GOOGLE_API_KEY"]
import pdfbot
from pdfbot import PDFBot
import uuid

st.set_page_config(page_title="PDF Q&A", page_icon="📄", layout="centered")


def get_bot():
    """One bot per browser session, isolated by session ID."""
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
    return PDFBot(session_id=st.session_state.session_id)


def ingest(bot, uploaded):
    """Save the upload to a temp file, index it, and report progress live."""
    suffix = os.path.splitext(uploaded.name)[1] or ".pdf"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(uploaded.getbuffer())
        tmp.close()

        bar = st.progress(0.0, text="Reading the PDF…")

        def progress(message, fraction=None):
            bar.progress(min(fraction or 0.0, 1.0), text=message)

        # load_pdf names the document after the file, so give the temp file
        # the real name rather than a random one.
        named = os.path.join(os.path.dirname(tmp.name), uploaded.name)
        os.replace(tmp.name, named)
        try:
            bot.load_pdf(named, progress=progress)
        finally:
            os.unlink(named)
        bar.progress(1.0, text="Done")
    except Exception:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)
        raise


st.title("📄 PDF Q&A")
st.caption("Upload a PDF, then ask questions about what's inside it.")

if not os.getenv("GOOGLE_API_KEY"):
    st.error("`GOOGLE_API_KEY` is not set. Add it to the `.env` file next to this script.")
    st.stop()

bot = get_bot()

if "history" not in st.session_state:
    st.session_state.history = []

with st.sidebar:
    st.header("Indexed documents")
    # Read these separately. Chunk counts are decoration — if they fail, that
    # must not cost you the document list, and losing the document list must
    # not cost you the ability to ask questions.
    try:
        indexed = bot.documents()
    except Exception as exc:
        indexed = []
        st.warning(f"Couldn't list documents: {exc}")

    try:
        counts = bot.chunk_counts()
    except Exception:
        counts = {}

    try:
        total_chunks = bot.collection.count()
    except Exception:
        total_chunks = 0

    if indexed:
        for name in indexed:
            row, action = st.columns([4, 1])
            detail = f"{counts[name]} chunks" if name in counts else ""
            row.write(f"**{name}**  \n{detail}")
            if action.button("🗑", key=f"del-{name}", help=f"Delete {name}"):
                st.session_state.pending_delete = name
                st.rerun()
    else:
        st.caption("Nothing indexed yet.")

    pending = st.session_state.get("pending_delete")
    if pending:
        st.warning(f"Delete **{pending}** and its {counts.get(pending, 0)} chunks?")
        yes, no = st.columns(2)
        if yes.button("Delete", type="primary", use_container_width=True):
            try:
                removed = bot.delete_document(pending)
            except Exception as exc:
                st.error(f"Couldn't delete that: {exc}")
            else:
                st.session_state.history = [
                    h for h in st.session_state.history if h["scope"] != pending
                ]
                st.toast(f"Deleted {pending} ({removed} chunks).")
            st.session_state.pop("pending_delete", None)
            st.rerun()
        if no.button("Cancel", use_container_width=True):
            st.session_state.pop("pending_delete", None)
            st.rerun()

    st.divider()
    scope = st.selectbox(
        "Search in",
        ["All documents"] + indexed,
        help="Limit answers to a single document, or search everything.",
    )
    passages = st.slider("Passages to retrieve", 1, 8, 3)

    st.divider()
    st.caption(
        f"Chunk size {pdfbot.CHUNK_WORDS} words · batch {pdfbot.BATCH_SIZE} · "
        f"model {pdfbot.CHAT_MODEL}"
    )

# --- upload ---------------------------------------------------------------

uploaded = st.file_uploader("Add a PDF", type=["pdf"])
if uploaded is not None:
    if st.button(f"Index “{uploaded.name}”", type="primary"):
        try:
            ingest(bot, uploaded)
        except ValueError as exc:  # no extractable text
            st.error(str(exc))
        except Exception as exc:
            text = str(exc)
            if "429" in text or "RESOURCE_EXHAUSTED" in text:
                st.error(
                    "Ran out of free-tier quota, and waiting for the window to "
                    "reset didn't clear it. Try again later, or use a smaller PDF."
                )
            elif any(s in text for s in ("SSL", "EOF", "Connection", "10053")):
                st.error(
                    "The connection to Google kept dropping. Check your network — "
                    "phone tethering, a VPN, or antivirus scanning HTTPS will do this."
                )
            else:
                st.error(f"Couldn't index that: {exc}")
        else:
            st.success(f"Indexed {uploaded.name}.")
            st.rerun()

# --- ask ------------------------------------------------------------------

st.divider()

if not total_chunks:
    st.info("Index a PDF above, then you can ask questions about it.")
else:
    question = st.text_input(
        "Your question",
        placeholder="What is the main topic of this document?",
    )
    if st.button("Ask", type="primary") and question.strip():
        doc_id = None if scope == "All documents" else scope
        try:
            with st.spinner("Searching the document…"):
                answer, chunks = bot.ask_question(
                    question, doc_id=doc_id, n_results=passages
                )
        except Exception as exc:
            st.error(f"Couldn't answer that: {exc}")
        else:
            st.session_state.history.insert(
                0, {"question": question, "answer": answer, "chunks": chunks,
                    "scope": scope}
            )

for i, entry in enumerate(st.session_state.history):
    st.markdown(f"**{entry['question']}**")
    st.write(entry["answer"])
    with st.expander(f"{len(entry['chunks'])} source passage(s) · {entry['scope']}"):
        for j, chunk in enumerate(entry["chunks"], 1):
            st.markdown(f"*Passage {j}*")
            st.caption(chunk[:600] + ("…" if len(chunk) > 600 else ""))
    if i < len(st.session_state.history) - 1:
        st.divider()

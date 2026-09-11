import os
import time

import chromadb
import google.genai as genai
from dotenv import load_dotenv
from google.genai.types import EmbedContentConfig
from pypdf import PdfReader  # pypdf is the maintained successor to PyPDF2

load_dotenv()

client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])

EMBED_MODEL = "gemini-embedding-001"   # current embedding model
CHAT_MODEL = "gemini-3.6-flash"        # current stable Flash model

CHUNK_WORDS = 500
# Overlap makes a sentence that straddles two chunks still findable, but every
# extra word is another embedded token. 0 keeps quota use at its minimum;
# 50-75 noticeably improves answers on prose at ~10-15% more quota.
CHUNK_OVERLAP = 0

# 10 chunks x 500 words is roughly 7,500 tokens per request. Going much higher
# (50 chunks = ~37k tokens) is rejected outright by the free tier with a 429.
BATCH_SIZE = 10

# The free tier meters tokens per minute, so a long PDF will run out partway
# through and recover once the window rolls over. Waiting is what lets a large
# document finish instead of failing at 60%.
QUOTA_WAIT = 62
QUOTA_RETRIES = 5
# Separate budget for dropped connections (flaky wifi, tethering, VPNs,
# antivirus intercepting TLS), which fail instantly rather than after a wait.
NETWORK_RETRIES = 8
NETWORK_BACKOFF = 2
NETWORK_BACKOFF_MAX = 15

# Keep the database next to this file, not wherever you happened to run python.
STORAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf_storage")


def _is_over_quota(exc):
    text = str(exc)
    return "429" in text or "RESOURCE_EXHAUSTED" in text


def _is_network_blip(exc):
    text = str(exc)
    return any(s in text for s in
               ("SSL", "EOF", "Connection", "10053", "503", "UNAVAILABLE", "timeout"))


def embed(contents, task_type, progress=None):
    """One embedding request, retried through quota windows and dropped links."""
    quota = network = 0
    while True:
        try:
            response = client.models.embed_content(
                model=EMBED_MODEL,
                contents=contents,
                config=EmbedContentConfig(task_type=task_type),
            )
            return [item.values for item in response.embeddings]
        except Exception as exc:
            if _is_over_quota(exc) and quota < QUOTA_RETRIES:
                quota += 1
                if progress:
                    progress(f"Free-tier quota reached — waiting {QUOTA_WAIT}s "
                             f"({quota}/{QUOTA_RETRIES})")
                time.sleep(QUOTA_WAIT)
            elif _is_network_blip(exc) and network < NETWORK_RETRIES:
                network += 1
                if progress and network == 3:
                    progress("Connection keeps dropping — still retrying")
                time.sleep(min(NETWORK_BACKOFF * (2 ** network), NETWORK_BACKOFF_MAX))
            else:
                raise


class PDFBot:
    def __init__(self, storage_path=STORAGE_PATH):
        self.chroma_client = chromadb.PersistentClient(path=storage_path)
        self.collection = self.chroma_client.get_or_create_collection(
            name="pdf_knowledge_base"
        )

    def chunk(self, text):
        words = text.split()
        if not words:
            return []
        step = max(1, CHUNK_WORDS - CHUNK_OVERLAP)
        chunks = []
        for i in range(0, len(words), step):
            chunk = " ".join(words[i:i + CHUNK_WORDS])
            if chunk:
                chunks.append(chunk)
            if i + CHUNK_WORDS >= len(words):
                break
        return chunks

    def load_pdf(self, pdf_path, progress=None):
        doc_id = os.path.splitext(os.path.basename(pdf_path))[0]

        with open(pdf_path, "rb") as file:
            reader = PdfReader(file)
            text = "".join(page.extract_text() or "" for page in reader.pages)

        chunks = self.chunk(text)
        if not chunks:
            raise ValueError(
                f"No text could be extracted from {doc_id}. If it is a scanned "
                f"PDF it is a picture of pages, and needs OCR before it can be read."
            )

        total = len(chunks)
        message = f"{doc_id}: {total} chunks to embed"
        print(f"[doc] {message}")
        if progress:
            progress(message, 0.0)

        for i in range(0, total, BATCH_SIZE):
            batch_chunks = chunks[i:i + BATCH_SIZE]

            # One API call for the whole batch...
            embeddings = embed(
                batch_chunks, "RETRIEVAL_DOCUMENT",
                progress=(lambda m: progress(m, i / total)) if progress else None,
            )

            # ...and one write for the whole batch. `upsert` rather than `add`
            # so re-loading the same PDF replaces its chunks instead of
            # erroring on duplicate ids.
            self.collection.upsert(
                ids=[f"{doc_id}_chunk_{i + j}" for j in range(len(batch_chunks))],
                embeddings=embeddings,
                documents=batch_chunks,
                metadatas=[{"source": doc_id, "chunk_index": i + j}
                           for j in range(len(batch_chunks))],
            )

            done = min(i + BATCH_SIZE, total)
            print(f"[doc]   embedded {done}/{total}")
            if progress:
                progress(f"Embedded {done} of {total} chunks", done / total)

        print(f"[doc] {doc_id} loaded and indexed successfully")
        return text

    def ask_question(self, question, doc_id=None, n_results=3):
        # Step 1: turn the question into an embedding
        question_embedding = embed(question, "RETRIEVAL_QUERY")[0]

        # Step 2: find the most relevant chunks
        results = self.collection.query(
            query_embeddings=[question_embedding],
            n_results=n_results,
            where={"source": doc_id} if doc_id else None,
        )
        documents = (results.get("documents") or [[]])[0]
        relevant_chunks = [doc for doc in documents if doc is not None]

        if not relevant_chunks:
            return "I don't have that information in this document.", []

        context = "\n\n".join(relevant_chunks)

        # Step 3: the strict prompt — answer from the context or admit ignorance
        full_prompt = f"""
You are a strict AI assistant. Answer the user's question based ONLY on the context below.
If the context doesn't contain the answer, say "I don't have that information in this document."

CONTEXT:
{context}

QUESTION: {question}

ANSWER:
"""

        # Step 4: ask Gemini
        response = client.models.generate_content(
            model=CHAT_MODEL, contents=full_prompt
        )
        return (response.text or "").strip(), relevant_chunks

    def documents(self):
        """Which PDFs are already indexed."""
        existing = self.collection.get(include=["metadatas"])
        return sorted({
            (m or {}).get("source")
            for m in (existing.get("metadatas") or [])
            if (m or {}).get("source")
        })

    def chunk_counts(self):
        """How many chunks each indexed document holds, keyed by name."""
        existing = self.collection.get(include=["metadatas"])
        counts = {}
        for meta in existing.get("metadatas") or []:
            source = (meta or {}).get("source")
            if source:
                counts[source] = counts.get(source, 0) + 1
        return counts

    def delete_document(self, doc_id):
        """Remove every chunk belonging to one document.

        Returns the number of chunks removed. Deleting a document it doesn't
        have is not an error — it just removes nothing.
        """
        before = self.collection.count()
        self.collection.delete(where={"source": doc_id})
        return before - self.collection.count()


# --- The start engine button ---
if __name__ == "__main__":
    pdf_bot = PDFBot()

    pdf_bot.load_pdf("riverbend_policy.pdf")

    input_question = input("Enter your question: ")
    answer, chunks = pdf_bot.ask_question(input_question)

    print(f"\nRetrieved {len(chunks)} relevant chunks.")
    for i, chunk in enumerate(chunks, 1):
        print(f"   --- Chunk {i} (first 150 chars) ---")
        print(f"   {chunk[:150]}...\n")

    print("=" * 60)
    print("GEMINI ANSWER:")
    print("=" * 60)
    print(answer)
    print("=" * 60)

# PDF Q&A

Ask questions about your own PDFs and get answers grounded in the document —
with the passages the answer came from shown alongside it, so you can check
nothing was invented.

Built on Google Gemini and ChromaDB. Supports multiple concurrent users with
full session isolation — no document crossover between visitors.

![Python](https://img.shields.io/badge/python-3.10%2B-blue)

---

## How it works

This is a small RAG (retrieval-augmented generation) pipeline:

1. **Extract** the text from your PDF with `pypdf`
2. **Split** it into overlapping-optional chunks of ~500 words
3. **Embed** each chunk into a vector with `gemini-embedding-001` and store it
   in a ChromaDB collection scoped to the current session
4. **Retrieve** the chunks most similar to your question when you ask one
5. **Answer** using `gemini-3.6-flash`, with a strict instruction to use only
   those chunks — if the answer isn't there, it says so rather than guessing

The point of step 5 is that the model can't wander off into things it half
remembers from training. Every answer is traceable to a passage you can read.

---

## Multi-user session isolation

Each browser session gets its own isolated ChromaDB collection, keyed by a
UUID generated at session start and stored in `st.session_state`. This means:

- Documents uploaded by one user are invisible to every other user
- Questions only search the current session's indexed documents
- Sessions are independent across tabs, devices and concurrent visitors

**One current limitation:** sessions are not persistent across browser restarts.
If a user closes and reopens the app, they get a new session and will need to
re-upload their PDFs. Persistent user accounts (via `st.login` with Google OIDC)
are a planned future improvement.

---

## Setup

You need Python 3.10 or newer.

```bash
pip install -r requirements.txt
```

Get a free Gemini API key from
[aistudio.google.com/apikey](https://aistudio.google.com/apikey), then create a
file called `.env` next to `pdfbot.py`:

```
GOOGLE_API_KEY=your-key-here
```

---

## Running it

### Web interface

```bash
streamlit run pdf_app.py
```

Opens at <http://localhost:8501>. Upload a PDF, watch the progress bar while it
indexes, then ask away.

The sidebar lists everything you've indexed with its chunk count, lets you scope
a question to one document or search all of them, and has a 🗑 button to delete
a document (with a confirm step) when you no longer want it searchable.

### From Python

```python
from pdfbot import PDFBot

bot = PDFBot()
bot.load_pdf("my_document.pdf")

answer, passages = bot.ask_question("What is the refund policy?")
print(answer)

bot.documents()                    # ['my_document']
bot.chunk_counts()                 # {'my_document': 42}
bot.delete_document("my_document") # 42  (chunks removed)
```

### From the command line

```bash
python pdfbot.py
```

---

## Working within the Gemini free tier

The free tier meters **tokens per minute**, and a long PDF will exhaust that
budget partway through. The code handles this rather than failing:

- Chunks are sent **10 per request** (~7,500 tokens). Larger batches get
  rejected outright — 50 chunks is ~37,000 tokens, which returns a 429 no
  matter how long you wait.
- When the per-minute budget runs out, it **waits ~62 seconds** for the window
  to reset and resumes from the same batch.
- Dropped connections are retried with backoff. Phone tethering, VPNs, and
  antivirus software that inspects HTTPS all cause these.

Measured on a 400-section test PDF:

```
98 chunks · all 98 stored · 86 seconds
  quota reached at chunk 50 → waited 62s → resumed and finished
```

So large PDFs work on the free tier; they just take a few minutes.

---

## Configuration

All in the constants at the top of `pdfbot.py`:

| Setting | Default | What it does |
|---|---|---|
| `CHUNK_WORDS` | `500` | Words per chunk |
| `CHUNK_OVERLAP` | `0` | Overlap between chunks. `50`–`75` gives noticeably better answers on prose, at ~10–15% more quota |
| `BATCH_SIZE` | `10` | Chunks per embedding request. Raising this past ~20 will hit free-tier limits |
| `QUOTA_WAIT` | `62` | Seconds to wait when the per-minute quota is spent |
| `CHAT_MODEL` | `gemini-3.6-flash` | Model that writes the answer |
| `EMBED_MODEL` | `gemini-embedding-001` | Model that indexes and searches |

---

## Your data

Everything stays local:

| What | Where |
|---|---|
| Text chunks and their embeddings | `pdf_storage/` (one sub-collection per session) |
| Your API key | `.env` |

Your PDF file itself is never uploaded to Google. What *is* sent, each time you
ask a question, is the text of your question and the handful of passages that
matched it — typically three short extracts. If a document is sensitive enough
that this matters, don't put it in.

To wipe all indexed data, delete the `pdf_storage/` folder.

---

## Limitations

- **Scanned PDFs won't work.** If the PDF is photographs of pages there is no
  text layer to extract. You'd need OCR first. The app tells you rather than
  failing silently.
- **Indexing blocks while it runs.** Fine for a personal tool; a background
  queue would be needed for high-traffic deployments.
- **Sessions are not persistent.** Closing and reopening the browser starts a
  fresh session. Re-upload your PDFs to continue.

---

## Files

| File | Purpose |
|---|---|
| `pdfbot.py` | The `PDFBot` class — extraction, chunking, embedding, retrieval, answering |
| `pdf_app.py` | Streamlit interface. Imports `PDFBot`; contains no logic of its own |
| `requirements.txt` | Dependencies |

---

## License

MIT — do what you like with it.

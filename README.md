# PDF Q&A

Ask questions about your own PDFs and get answers grounded in the document —
with the passages the answer came from shown alongside it, so you can check
nothing was invented.

Built on Google Gemini and ChromaDB. Runs on your machine; your documents stay
on your disk.

![Python](https://img.shields.io/badge/python-3.10%2B-blue)

> ### ⚠️ Run this locally, not on a public server
>
> This is a **single-user tool**. It has no logins and no separation between
> visitors, so on a shared address everyone sees everyone else's documents.
> [Details below](#why-this-is-single-user-only).

---

## How it works

This is a small RAG (retrieval-augmented generation) pipeline:

1. **Extract** the text from your PDF with `pypdf`
2. **Split** it into overlapping-optional chunks of ~500 words
3. **Embed** each chunk into a vector with `gemini-embedding-001` and store it
   in a local ChromaDB database
4. **Retrieve** the chunks most similar to your question when you ask one
5. **Answer** using `gemini-3.6-flash`, with a strict instruction to use only
   those chunks — if the answer isn't there, it says so rather than guessing

The point of step 5 is that the model can't wander off into things it half
remembers from training. Every answer is traceable to a passage you can read.

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
| Text chunks and their embeddings | `pdf_storage/` |
| Your API key | `.env` |

Your PDF file itself is never uploaded to Google. What *is* sent, each time you
ask a question, is the text of your question and the handful of passages that
matched it — typically three short extracts. If a document is sensitive enough
that this matters, don't put it in.

To wipe the index, delete the `pdf_storage/` folder.

---

## Limitations

- **Scanned PDFs won't work.** If the PDF is photographs of pages there is no
  text layer to extract. You'd need OCR first. The app tells you rather than
  failing silently.
- **Indexing blocks while it runs.** Fine for a personal tool; it would need a
  background queue to serve several people at once.
See also [Why this is single-user only](#why-this-is-single-user-only).

---

## Why this is single-user only

If you host this where two people can reach it, **they share one document
library**. This is not a subtle edge case — it is how the app is built:

- `@st.cache_resource` on `get_bot()` gives the entire server **one** `PDFBot`
  instance. That is what the decorator is for.
- That bot has **one** ChromaDB collection. `documents()` returns everything in
  it, with no record of who uploaded what.
- The delete button performs no ownership check.

So a second visitor can:

| | |
|---|---|
| See your documents | Every filename appears in their sidebar |
| Read their contents | Questions return real passages of your text |
| Delete them | The 🗑 button works on anyone's documents |
| Spend your quota | Every question bills to the `GOOGLE_API_KEY` in your `.env` |

The one thing that *is* private is the on-screen Q&A history —
`st.session_state` is per-session, so people don't see each other's questions.
That is small consolation when they can read each other's source documents.

**Making it safe for more than one person** means adding real per-user
ownership — `st.login` with Google OIDC (Streamlit 1.42+), documents tagged to
the signed-in account, and every query and delete filtered by owner. That is a
feature to build deliberately, not a config flag.

Until then: run it on `localhost`, or give people the code so they can run their
own copy.

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

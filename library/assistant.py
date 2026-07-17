"""Conversational document assistant: Claude Messages API + tool use.

Searches Document metadata (title/code/folder/section/notes) and, when a
question needs it, reads the actual file content (PDF/DOCX/XLSX/CSV/MD/TXT).
All DB access goes through visible_documents(user), so the role-based access
rules in views.py are enforced for the agent too — it can only read a
document's content if that document was already returned by search_documents
for this user.
"""
import json

import anthropic
from django.conf import settings
from django.db.models import Case, IntegerField, Q, Value, When
from django.urls import reverse

from .content import extract_document_text
from .views import visible_documents, visible_sections

MODEL = "claude-haiku-4-5"
MAX_TOOL_ITERATIONS = 4

SYSTEM_PROMPT = (
    "You are a helpful, personable assistant for VIS-Recruit's QMS document library. "
    "Talk like a knowledgeable colleague, not a search engine — acknowledge what "
    "the person is actually asking for, use natural phrasing, and keep a warm, "
    "professional tone (this is a compliance system, so stay accurate and concise, "
    "not chatty). "
    "Always call search_documents first to find candidate documents — never invent "
    "document names or answer from prior knowledge. When the question needs an actual "
    "answer drawn from inside a document (a phone number, a procedure step, a policy "
    "detail, a date on a form, a summary of what a record says), call read_document on "
    "the most relevant result(s) and answer using that content directly in plain, "
    "common-sense language — don't just hand back a list of links when the person is "
    "clearly asking a real question that the document can answer. If read_document "
    "reports the file can't be read (unsupported format or a scanned image with no "
    "extractable text), say so plainly and point them to the document link instead of "
    "guessing. You can only read documents that search_documents already returned for "
    "this person, so you never see anything they aren't themselves allowed to access. "
    "If a query is too vague to search usefully, ask a brief, friendly clarifying "
    "question instead of guessing."
)

def build_search_tool(user):
    """Rebuilt per call (not a frozen module-level constant) so a Section
    added via admin is usable by the agent immediately, without a restart.
    The section enum is scoped to this user's visible_sections(), so the
    agent can't even suggest filtering by a section hidden from their role."""
    return {
        "name": "search_documents",
        "description": (
            "Search the controlled document library by keywords, section, and/or "
            "folder path substring. Searches metadata only (title, code, folder, "
            "notes). Always call this before answering questions about what "
            "documents exist; do not answer from prior knowledge or guess "
            "document names."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "string",
                    "description": "Free-text keywords to match against title, code, folder, and notes",
                },
                "section": {
                    "type": "string",
                    "enum": list(visible_sections(user).values_list("code", flat=True)),
                    "description": "Restrict results to one library section",
                },
                "folder_contains": {
                    "type": "string",
                    "description": "Substring to match within the folder path, e.g. a year like 2024 or a topic like INTERNAL-AUDIT",
                },
            },
            "required": [],
        },
    }

READ_TOOL = {
    "name": "read_document",
    "description": (
        "Fetch and read the actual text content of one specific document, "
        "identified by its id from a previous search_documents result. Use "
        "this whenever the question needs details that are actually written "
        "inside the document (e.g. 'what phone number is listed', 'what does "
        "step 3 say', 'summarize this procedure') rather than just its "
        "metadata. Only works for documents the person is already allowed to "
        "see. Not every file format can be read (e.g. scanned images, legacy "
        ".doc) — if content isn't available, say so and point the person to "
        "the document link instead of guessing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "document_id": {
                "type": "integer",
                "description": "The id of the document to read, from a prior search_documents result",
            },
        },
        "required": ["document_id"],
    },
}


def search_documents_tool(user, keywords="", section="", folder_contains=""):
    """The agent's metadata search. Always starts from visible_documents(user)
    so employee/auditor roles never see draft/restricted documents via the agent."""
    qs = visible_documents(user)
    if section:
        qs = qs.filter(section=section)
    if folder_contains:
        qs = qs.filter(folder__icontains=folder_contains)
    if keywords:
        # OR across terms (any matching term counts as a hit) so a natural,
        # multi-word question doesn't require every word to appear in the
        # same document — ranked by how many terms matched, then recency.
        term_match = Q()
        score = None
        for term in keywords.split():
            term_q = (
                Q(title__icontains=term) | Q(code__icontains=term) |
                Q(folder__icontains=term) | Q(notes__icontains=term)
            )
            term_match |= term_q
            hit = Case(When(term_q, then=Value(1)), default=Value(0), output_field=IntegerField())
            score = hit if score is None else score + hit
        qs = qs.filter(term_match).annotate(_match_score=score).order_by("-_match_score", "-issue_date")
    else:
        qs = qs.order_by("-issue_date")
    qs = qs[:20]
    return [
        {
            "id": d.pk,
            "code": d.code,
            "title": d.title,
            "section": d.section,
            "folder": d.folder,
            "revision": d.revision,
            "issue_date": d.issue_date.isoformat() if d.issue_date else None,
            "url": reverse("library:download", args=[d.pk]),
        }
        for d in qs
    ]


def read_document_tool(user, document_id):
    """Enforces access control: only documents visible to this user can be read."""
    doc = visible_documents(user).filter(pk=document_id).first()
    if not doc:
        return {"error": "Document not found or not accessible."}

    text = extract_document_text(doc)
    if text is None:
        return {
            "id": doc.pk, "title": doc.title, "code": doc.code,
            "error": "This file format can't be read by the assistant — direct the person to the document link.",
        }
    if text == "":
        return {
            "id": doc.pk, "title": doc.title, "code": doc.code,
            "error": "No extractable text found (the file may be a scanned image).",
        }
    return {"id": doc.pk, "title": doc.title, "code": doc.code, "content": text}


def run_agent_turn(user, message, history):
    """
    history: list of {"role": "user"/"assistant", "content": str} plain-text
             turns from prior exchanges in this session.
    Returns (reply_text, documents, new_history).
    """
    if not settings.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=30.0)

    messages = list(history) + [{"role": "user", "content": message}]
    all_docs = {}
    response = None
    tools = [build_search_tool(user), READ_TOOL]

    for _ in range(MAX_TOOL_ITERATIONS):
        response = client.messages.create(
            model=MODEL,
            max_tokens=1536,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        )
        if response.stop_reason != "tool_use":
            break

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            if block.name == "search_documents":
                results = search_documents_tool(user, **block.input)
                for r in results:
                    all_docs[r["id"]] = r
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(results),
                })
            elif block.name == "read_document":
                result = read_document_tool(user, **block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                })
        messages.append({"role": "user", "content": tool_results})
    else:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1536,
            system=SYSTEM_PROMPT,
            messages=messages,
        )

    reply_text = next((b.text for b in response.content if b.type == "text"), "") if response else ""
    new_history = (history + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": reply_text},
    ])[-6:]
    return reply_text, list(all_docs.values())[:25], new_history

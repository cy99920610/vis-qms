"""Conversational document-finder: Claude Messages API + tool use.

Searches Document metadata only (title/code/folder/section/notes) — never
file contents. All DB access goes through visible_documents(user), so the
role-based access rules in views.py are enforced for the agent too.
"""
import json

import anthropic
from django.conf import settings
from django.db.models import Q
from django.urls import reverse

from .models import SECTIONS
from .views import visible_documents

MODEL = "claude-haiku-4-5"
MAX_TOOL_ITERATIONS = 3

SYSTEM_PROMPT = (
    "You are a helpful, personable assistant for VIS-Recruit's QMS document library. "
    "Talk like a knowledgeable colleague, not a search engine — acknowledge what "
    "the person is actually asking for, use natural phrasing, and keep a warm, "
    "professional tone (this is a compliance system, so stay accurate and concise, "
    "not chatty). You only have access to document metadata (title, code, section, "
    "folder, revision, issue date) via the search_documents tool — you never see "
    "file contents, so never invent document names or details. Always call "
    "search_documents before answering questions about what documents exist. If a "
    "query is too vague to search usefully, ask a brief, friendly clarifying "
    "question instead of guessing. When you find matches, briefly say what you "
    "found in plain language before listing them."
)

SEARCH_TOOL = {
    "name": "search_documents",
    "description": (
        "Search the controlled document library by keywords, section, and/or "
        "folder path substring. Searches metadata only (title, code, folder, "
        "notes) — never file contents. Always call this before answering "
        "questions about what documents exist; do not answer from prior "
        "knowledge or guess document names."
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
                "enum": [code for code, _ in SECTIONS],
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


def search_documents_tool(user, keywords="", section="", folder_contains=""):
    """The agent's only DB access point. Always starts from visible_documents(user)
    so employee/auditor roles never see draft/restricted documents via the agent."""
    qs = visible_documents(user)
    if section:
        qs = qs.filter(section=section)
    if folder_contains:
        qs = qs.filter(folder__icontains=folder_contains)
    if keywords:
        for term in keywords.split():
            qs = qs.filter(
                Q(title__icontains=term) | Q(code__icontains=term) |
                Q(folder__icontains=term) | Q(notes__icontains=term)
            )
    qs = qs.order_by("-issue_date")[:20]
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


def run_agent_turn(user, message, history):
    """
    history: list of {"role": "user"/"assistant", "content": str} plain-text
             turns from prior exchanges in this session.
    Returns (reply_text, documents, new_history).
    """
    if not settings.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=20.0)

    messages = list(history) + [{"role": "user", "content": message}]
    all_docs = {}
    response = None

    for _ in range(MAX_TOOL_ITERATIONS):
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=[SEARCH_TOOL],
            messages=messages,
        )
        if response.stop_reason != "tool_use":
            break

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type == "tool_use" and block.name == "search_documents":
                results = search_documents_tool(user, **block.input)
                for r in results:
                    all_docs[r["id"]] = r
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(results),
                })
        messages.append({"role": "user", "content": tool_results})
    else:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=messages,
        )

    reply_text = next((b.text for b in response.content if b.type == "text"), "") if response else ""
    new_history = (history + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": reply_text},
    ])[-6:]
    return reply_text, list(all_docs.values())[:25], new_history

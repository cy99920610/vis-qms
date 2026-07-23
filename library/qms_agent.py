"""QMS Document Control Agent — Claude Messages API + tool use.

A second, separate agent from assistant.py's general document-finder: this
one is admin-only and specialised for document-control/maintenance
guidance — what fields must be set/updated when a document is created or
revised, and what a specific Watchdog finding means and how to fix it. It
never edits anything itself; it only reads Document/watchdog data and
advises the QMS Manager, who makes the change in the Django admin.
"""
import json

import anthropic
from django.conf import settings

from .assistant import build_search_tool, search_documents_tool
from .watchdog import run_watchdog_checks

MODEL = "claude-haiku-4-5"
MAX_TOOL_ITERATIONS = 4

SYSTEM_PROMPT = (
    "You are the VIS-QMS Document Control Agent, an assistant for the QMS Manager/Admin using the "
    "Document Control / Maintenance Tool. Your job is to advise on what must be checked or updated "
    "when a controlled document is created or revised, and to explain Watchdog findings and how to "
    "resolve them. You are read-only — you never change a document yourself; you tell the QMS "
    "Manager exactly what to do in the Django admin.\n\n"
    "Ground every answer in the actual VIS-QMS document model, which has these controlled fields: "
    "code (e.g. QP-03, QM-01), title, revision, section (top-level library category), folder "
    "(must start with the section's code), issue_date (the revision/approval date), is_final "
    "(Final = approved, visible to employee/auditor roles; unticked = Draft, hidden from them), "
    "notes, and hidden_from_groups (per-document visibility override).\n\n"
    "When creating a NEW controlled document, the checklist is: assign the correct code following "
    "the existing series (QP-, QM-, OP-, QR- etc.), set title and revision (start at a real revision "
    "marker, not blank), set section AND a folder that starts with that section's code, set issue_date "
    "to the actual approval date, leave is_final unticked until it is actually approved, and upload the "
    "final approved version as a PDF once approved (working/source files belong in a source-editable "
    "or Draft folder with is_final off).\n\n"
    "When REVISING an existing document, the checklist is: bump the revision field to a new value "
    "(never silently overwrite an approved revision's meaning), update issue_date to the new revision's "
    "approval date, keep the same code (codes identify the document across its whole history — they "
    "should not change on revision), only mark the new version is_final=True once approved, and the "
    "previously approved revision should be moved to an Obsolete folder/section (or explicitly marked "
    "not final) so there is never more than one Final version of the same code at once — this is "
    "exactly what the duplicate_code and obsolete_marked_final Watchdog checks catch.\n\n"
    "Use get_watchdog_findings to see current, real inconsistencies (optionally filtered to one "
    "document/code) before answering questions like 'what's wrong with QP-04' or 'is this document "
    "ready to be marked final' — don't guess, look it up. Use search_documents to find a document's "
    "current metadata when asked about a specific code or title. Answer plainly and concretely: name "
    "the exact field to change and what value it should have, not generic advice."
)

FINDINGS_TOOL = {
    "name": "get_watchdog_findings",
    "description": (
        "Get current Document Control Watchdog findings — real inconsistencies detected across the "
        "whole document library (missing revision, missing approval date, duplicate code, wrong "
        "folder/status, final document not PDF, draft/source files visible incorrectly, obsolete "
        "documents marked final, missing PDF final version, mismatched QMS Task references). Always "
        "call this before answering a question about what's wrong with a document, or before telling "
        "someone their document is ready to publish as final."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "code_or_title_contains": {
                "type": "string",
                "description": "Optional substring to filter findings to one document/code, e.g. 'QP-04'",
            },
            "category": {
                "type": "string",
                "description": "Optional: restrict to one finding category",
                "enum": [
                    "missing_revision", "missing_approval_date", "duplicate_code",
                    "folder_status_mismatch", "final_not_pdf", "draft_visible_incorrectly",
                    "obsolete_marked_final", "missing_pdf_final", "mismatched_reference",
                ],
            },
        },
        "required": [],
    },
}


def get_watchdog_findings_tool(code_or_title_contains="", category=""):
    findings, summary = run_watchdog_checks()
    if category:
        findings = [f for f in findings if f["category"] == category]
    if code_or_title_contains:
        needle = code_or_title_contains.lower()
        findings = [
            f for f in findings
            if needle in (f["code"] or "").lower() or needle in (f["title"] or "").lower()
        ]
    return {
        "total_matching": len(findings),
        "findings": findings[:30],
        "summary_by_category": {k: v["count"] for k, v in summary.items()},
    }


def run_qms_agent_turn(user, message, history):
    """Same shape as assistant.run_agent_turn: (reply_text, documents, new_history)."""
    if not settings.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=30.0)

    messages = list(history) + [{"role": "user", "content": message}]
    all_docs = {}
    response = None
    tools = [build_search_tool(user), FINDINGS_TOOL]

    for _ in range(MAX_TOOL_ITERATIONS):
        response = client.messages.create(
            model=MODEL, max_tokens=1536, system=SYSTEM_PROMPT, tools=tools, messages=messages,
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
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(results)})
            elif block.name == "get_watchdog_findings":
                result = get_watchdog_findings_tool(**block.input)
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result)})
        messages.append({"role": "user", "content": tool_results})
    else:
        response = client.messages.create(model=MODEL, max_tokens=1536, system=SYSTEM_PROMPT, messages=messages)

    reply_text = next((b.text for b in response.content if b.type == "text"), "") if response else ""
    new_history = (history + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": reply_text},
    ])[-6:]
    return reply_text, list(all_docs.values())[:25], new_history

"""send_report_email tool — secure, audited report email delivery (Flow B).

Delivers Excel/PDF report bundles and verified summaries via Microsoft Graph
(``sendMail``) or SMTP (``STARTTLS``) with strict domain allowlisting, external
send confirmation gates, ``PBI_WORKSPACE_ROOT`` attachment containment, and
SHA-256 email hashing so plaintext addresses never enter audit records.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import mimetypes
import os
import re
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from powerbi_orchestrator_mcp.tools._tabular_io import compute_file_sha256
from powerbi_orchestrator_mcp.validation.path_safety import validate_safe_pbip_path

EMAIL_ALLOWED_DOMAINS_ENV = "PBI_EMAIL_ALLOWED_DOMAINS"
INTERNAL_DOMAINS_ENV = "PBI_EMAIL_INTERNAL_DOMAINS"
MAX_ATTACHMENT_BYTES_DEFAULT = 10 * 1024 * 1024

_SIMPLE_EMAIL_RE = re.compile(r"^[^@\s<>]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})$")
_EMBEDDED_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


class SendReportEmailInput(BaseModel):
    """Input schema for ``send_report_email``."""

    recipients_to: list[str]
    recipients_cc: list[str] = Field(default_factory=list)
    subject: str
    body_markdown: str
    attachment_paths: list[str] = Field(default_factory=list)
    audience_profile: Literal["internal_ops", "internal_admin", "external_client"] = "internal_ops"
    report_cadence: Literal["daily", "weekly", "monthly"] = "daily"
    confirm_external_send: bool = False
    transport: Literal["auto", "graph", "smtp"] = "auto"
    sender_address: str | None = None
    dry_run: bool = True


class SendReportEmailResult(BaseModel):
    """Output schema for ``send_report_email`` (no plaintext emails returned)."""

    status: Literal["sent", "dry_run", "blocked", "failed"]
    dry_run: bool
    transport_used: Literal["graph", "smtp", "none"] = "none"
    recipient_hashes: list[str] = Field(default_factory=list)
    attachment_hashes: dict[str, str] = Field(default_factory=dict)
    message_id: str | None = None
    blocked_reasons: list[str] = Field(default_factory=list)


def _hash_email(address: str) -> str:
    digest = hashlib.sha256(address.strip().lower().encode("utf-8")).hexdigest()[:8]
    return f"<email_hash:{digest}>"


def _scrub_emails(text: str) -> str:
    return _EMBEDDED_EMAIL_RE.sub(lambda m: _hash_email(m.group(0)), text)


def _parse_domains(env_var: str) -> set[str]:
    raw = os.environ.get(env_var, "")
    return {d.strip().lower().lstrip("@") for d in raw.split(",") if d.strip()}


def _extract_domain(address: str) -> str | None:
    match = _SIMPLE_EMAIL_RE.match(address.strip())
    return match.group(1).lower() if match else None


async def _send_via_smtp(
    *,
    sender: str,
    to_addrs: list[str],
    cc_addrs: list[str],
    subject: str,
    body_markdown: str,
    attachments: list[Path],
) -> str:
    host = os.environ.get("PBI_SMTP_HOST", "").strip()
    port = int(os.environ.get("PBI_SMTP_PORT", "587"))
    user = os.environ.get("PBI_SMTP_USER", "").strip()
    password = os.environ.get("PBI_SMTP_PASSWORD", "")
    if not host:
        raise RuntimeError("PBI_SMTP_HOST is not configured")

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(to_addrs)
    if cc_addrs:
        msg["Cc"] = ", ".join(cc_addrs)
    msg["Subject"] = subject
    msg.set_content(body_markdown)

    for att in attachments:
        mime, _ = mimetypes.guess_type(att.name)
        maintype, subtype = (
            mime.split("/", 1) if mime and "/" in mime else ("application", "octet-stream")
        )
        msg.add_attachment(att.read_bytes(), maintype=maintype, subtype=subtype, filename=att.name)

    def _blocking_send() -> str:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.ehlo()
            if port in {587, 25} and smtp.has_extn("starttls"):
                smtp.starttls()
                smtp.ehlo()
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)
        return str(
            msg.get("Message-ID")
            or f"smtp-{hashlib.sha256(subject.encode('utf-8')).hexdigest()[:12]}"
        )

    return await asyncio.to_thread(_blocking_send)


async def _send_via_graph(
    *,
    sender: str,
    to_addrs: list[str],
    cc_addrs: list[str],
    subject: str,
    body_markdown: str,
    attachments: list[Path],
    graph_client: Any = None,
) -> str:
    token = os.environ.get("PBI_GRAPH_ACCESS_TOKEN", "").strip()
    if not token:
        from azure.identity import DefaultAzureCredential

        cred = DefaultAzureCredential()
        token = cred.get_token("https://graph.microsoft.com/.default").token

    payload: dict[str, Any] = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body_markdown},
            "toRecipients": [{"emailAddress": {"address": a}} for a in to_addrs],
            "ccRecipients": [{"emailAddress": {"address": a}} for a in cc_addrs],
            "attachments": [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": att.name,
                    "contentBytes": base64.b64encode(att.read_bytes()).decode("ascii"),
                }
                for att in attachments
            ],
        },
        "saveToSentItems": True,
    }
    url = f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    if graph_client is not None:
        resp = await graph_client.post(url, json=payload, headers=headers)
    else:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, json=payload, headers=headers)

    if resp.status_code >= 400:
        raise RuntimeError(f"Graph sendMail returned HTTP {resp.status_code}")
    return str(
        resp.headers.get("request-id")
        or f"graph-{hashlib.sha256(subject.encode('utf-8')).hexdigest()[:12]}"
    )


async def send_report_email(
    recipients_to: list[str],
    subject: str,
    body_markdown: str,
    recipients_cc: list[str] | None = None,
    attachment_paths: list[str] | None = None,
    audience_profile: Literal["internal_ops", "internal_admin", "external_client"] = "internal_ops",
    report_cadence: Literal["daily", "weekly", "monthly"] = "daily",  # noqa: ARG001
    confirm_external_send: bool = False,
    transport: Literal["auto", "graph", "smtp"] = "auto",
    sender_address: str | None = None,
    dry_run: bool = True,
    max_attachment_bytes: int = MAX_ATTACHMENT_BYTES_DEFAULT,
    graph_client: Any = None,
    smtp_sender: Any = None,
) -> SendReportEmailResult:
    """Validate recipients/attachments and send a report bundle securely."""
    cc_list = list(recipients_cc or [])
    all_recipients = [*recipients_to, *cc_list]
    recipient_hashes = [_hash_email(addr) for addr in all_recipients]
    blocked: list[str] = []

    if not recipients_to:
        blocked.append("recipients_to cannot be empty")

    allowed_domains = _parse_domains(EMAIL_ALLOWED_DOMAINS_ENV)
    internal_domains = _parse_domains(INTERNAL_DOMAINS_ENV)

    if not dry_run and not allowed_domains:
        blocked.append(f"{EMAIL_ALLOWED_DOMAINS_ENV} must be configured before live sending")

    has_external_recipient = audience_profile == "external_client"
    for addr in all_recipients:
        dom = _extract_domain(addr)
        if dom is None:
            blocked.append(f"invalid recipient address {_hash_email(addr)}")
            continue
        if allowed_domains and dom not in allowed_domains:
            blocked.append(f"recipient domain '{dom}' not in {EMAIL_ALLOWED_DOMAINS_ENV}")
        if internal_domains and dom not in internal_domains:
            has_external_recipient = True

    if has_external_recipient and not confirm_external_send:
        blocked.append(
            "external_client / external domain delivery requires confirm_external_send=True"
        )

    safe_attachments: list[Path] = []
    attachment_hashes: dict[str, str] = {}
    total_bytes = 0
    for raw_path in attachment_paths or []:
        safe = validate_safe_pbip_path(raw_path, must_be_dir=False)
        if not safe.exists() or not safe.is_file():
            blocked.append(f"attachment not found: {raw_path}")
            continue
        size = safe.stat().st_size
        total_bytes += size
        safe_attachments.append(safe)
        attachment_hashes[str(safe)] = compute_file_sha256(safe)

    if total_bytes > max_attachment_bytes:
        blocked.append(
            f"total attachment size {total_bytes} bytes exceeds limit {max_attachment_bytes}"
        )

    if blocked:
        return SendReportEmailResult(
            status="blocked",
            dry_run=dry_run,
            transport_used="none",
            recipient_hashes=recipient_hashes,
            attachment_hashes=attachment_hashes,
            blocked_reasons=blocked,
        )

    chosen_transport: Literal["graph", "smtp"] = (
        "graph"
        if transport == "graph"
        or (
            transport == "auto"
            and (
                graph_client is not None
                or bool(os.environ.get("PBI_GRAPH_ACCESS_TOKEN", "").strip())
            )
        )
        else "smtp"
    )

    if dry_run:
        return SendReportEmailResult(
            status="dry_run",
            dry_run=True,
            transport_used=chosen_transport,
            recipient_hashes=recipient_hashes,
            attachment_hashes=attachment_hashes,
        )

    effective_sender = sender_address or os.environ.get("PBI_EMAIL_SENDER", "").strip()
    if not effective_sender or _extract_domain(effective_sender) is None:
        return SendReportEmailResult(
            status="blocked",
            dry_run=False,
            transport_used="none",
            recipient_hashes=recipient_hashes,
            attachment_hashes=attachment_hashes,
            blocked_reasons=["sender_address / PBI_EMAIL_SENDER is missing or invalid"],
        )

    try:
        if chosen_transport == "graph":
            msg_id = await _send_via_graph(
                sender=effective_sender,
                to_addrs=recipients_to,
                cc_addrs=cc_list,
                subject=subject,
                body_markdown=body_markdown,
                attachments=safe_attachments,
                graph_client=graph_client,
            )
        elif smtp_sender is not None:
            msg_id = str(
                smtp_sender(
                    sender=effective_sender,
                    to_addrs=recipients_to,
                    cc_addrs=cc_list,
                    subject=subject,
                    body_markdown=body_markdown,
                    attachments=safe_attachments,
                )
            )
        else:
            msg_id = await _send_via_smtp(
                sender=effective_sender,
                to_addrs=recipients_to,
                cc_addrs=cc_list,
                subject=subject,
                body_markdown=body_markdown,
                attachments=safe_attachments,
            )
    except Exception as exc:  # noqa: BLE001
        return SendReportEmailResult(
            status="failed",
            dry_run=False,
            transport_used=chosen_transport,
            recipient_hashes=recipient_hashes,
            attachment_hashes=attachment_hashes,
            blocked_reasons=[_scrub_emails(f"email delivery failed: {exc}")],
        )

    return SendReportEmailResult(
        status="sent",
        dry_run=False,
        transport_used=chosen_transport,
        recipient_hashes=recipient_hashes,
        attachment_hashes=attachment_hashes,
        message_id=msg_id,
    )


__all__ = [
    "EMAIL_ALLOWED_DOMAINS_ENV",
    "INTERNAL_DOMAINS_ENV",
    "SendReportEmailInput",
    "SendReportEmailResult",
    "send_report_email",
]

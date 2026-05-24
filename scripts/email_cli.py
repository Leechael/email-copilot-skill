#!/usr/bin/env python3
"""
Unified Email CLI for email-copilot skill.
Supports multi-account operations, email management, filter operations, attachments, and sending.
"""
import sys
import os
import json
import time
import base64
import argparse
import mimetypes
import re
import html
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from email.utils import parsedate_to_datetime, parseaddr
from datetime import datetime, timedelta, timezone

# Add skill directory to sys.path for local imports
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SKILL_DIR)
from gmail_client import GmailClient, AuthExpiredError, get_available_accounts, CONFIG_PATH


def get_client(account: str = None) -> GmailClient:
    """Get authenticated Gmail client for specified account (non-interactive)."""
    client = GmailClient(account=account)
    client.authenticate(interactive=False)
    return client


# =============================================================================
# Account Management
# =============================================================================

def cmd_accounts(args):
    """List all configured accounts."""
    accounts = get_available_accounts()
    if not accounts:
        print(json.dumps({"accounts": [], "count": 0}))
        return

    try:
        import tomllib
    except ImportError:
        import tomli as tomllib

    try:
        with open(CONFIG_PATH, "rb") as f:
            config = tomllib.load(f)
        default = config.get("gmail", {}).get("default_account", "default")
    except Exception as e:
        print(f"Warning: Could not read config: {e}", file=sys.stderr)
        default = "default"

    output = []
    for name, info in accounts.items():
        output.append({
            "name": name,
            "email": info.get("email", "(not authenticated)"),
            "is_default": name == default,
        })

    print(json.dumps({"accounts": output, "count": len(output)}, indent=2))


# =============================================================================
# Email Operations
# =============================================================================

# =============================================================================
# Display contracts for `inbox`, `search`, and `thread`
#
# `inbox` and `search` share the same per-thread rendering and pagination —
# see `_run_thread_listing`. The only differences are the filter passed to
# threads.list and how the summary line is built.
#
# `inbox` — Gmail-client-style overview
#   - Aggregates by thread, never by message. One row per conversation.
#   - Sorted by the thread's latest-message time (Gmail API default).
#   - Header summary uses the INBOX label's `threadsTotal`/`threadsUnread` so
#     counts match what Gmail's own UI shows (same-thread duplicates collapsed).
#   - Each row shows the LATEST message's metadata (id, from, to, subject,
#     date, reply_to when distinct from from). Replying acts on that id.
#   - Unread display:
#       multi-message thread → `unread: N/total` (e.g. 3/20)
#       single unread        → `unread: yes`
#       single read          → line omitted
#   - User labels rendered as `[[name]]` (system labels INBOX/UNREAD/CATEGORY_*
#     filtered out via type=="user").
#   - Pagination: --limit (default 100) / --skip (walks pages, slow) /
#     --cursor (page token from a prior call's `next_cursor`, efficient).
#   - Footer: blank line, then `page: current/total`, plus `next_cursor: ...`
#     when more pages exist.
#
# `search` — same rendering as inbox, with a Gmail query instead of label:INBOX
#   - Summary uses Gmail's `resultSizeEstimate` (an approximation), prefixed
#     with `~` to signal that. Page count is derived from the estimate, so the
#     `total` denominator may drift by a few near edges.
#   - Query syntax = Gmail search operators. Pass them as a single shell arg
#     (quote when there are spaces). Operators are combined by space = AND.
#
#     Location:      in:inbox|trash|spam|sent|drafts|anywhere
#                    label:Name | label:Parent/Child | -label:Name
#                    category:primary|promotions|updates|social|forums
#     State:         is:unread|read|starred|important|snoozed|muted
#     People:        from:addr  to:addr  cc:  bcc:  deliveredto:
#                    list:dev@yourdomain.example
#     Subject/body:  subject:foo  subject:"quarterly review"
#                    (bare keywords search full text)
#     Time:          newer_than:3d|m|y  older_than:7d
#                    after:2026/01/01   before:2026/03/01
#     Attachments:   has:attachment|drive|document
#                    filename:pdf  filename:invoice.pdf
#                    larger:5M  smaller:200K
#     Logic:         OR (uppercase)   -term (exclude)
#                    {a b c} = a OR b OR c
#                    ("exact phrase")   AROUND N (proximity)
#
#     Common recipes:
#       "in:inbox is:unread label:Notifiers/github"
#       "from:support@yourdomain.example newer_than:2d"
#       "is:unread -category:promotions -label:Newsletters"
#       "has:attachment filename:pdf newer_than:30d"
#       "subject:invoice OR subject:receipt"
#
# `thread` — full conversation expanded
#   - Default --limit 20 from the newest end; --skip moves into deeper history.
#   - Header `subject` is the FIRST (oldest) message's subject — the original
#     topic, not "Re:" chains.
#   - Per message: full body, headers (from/to/cc/reply_to), and attachments.
#     Bodies pass through clean_body (strip zero-width chars, collapse blank
#     runs) then gfm_quote_body (attribution lines like "On … wrote:" mark the
#     start of an inline reply quote; subsequent lines get `> ` prefixes,
#     nested quotes deepen the prefix).
#   - Messages printed chronologically within the page, separated by
#     `--- message N/total ---`. Footer: `shown: A-B/total`.
#
# `labels` and `filters` follow the same text style:
#   - Listings start with `account:` + a `labels:` / `filters:` summary line.
#   - User labels render as `[[name]]`; system labels (INBOX, STARRED, …) as
#     bare uppercase, matching how they appear in Gmail's API.
#   - Filters are rendered as `if X / then Y` blocks. Common system-label
#     actions get verb names: INBOX in remove → `archive`, UNREAD in remove →
#     `mark_read`, STARRED → `star`, IMPORTANT → `mark_important`, TRASH/SPAM
#     → `trash`/`spam`. Anything else falls back to `then label: [[name]]` or
#     `then remove_label: SYSNAME`.
#   - Mutations (`labels create/delete/rename`, `filters add/delete`) emit
#     short `account:` + action confirmation lines. `filters add` re-renders
#     the newly created filter using the same `if/then` formatter so the
#     caller sees exactly what got installed.
# =============================================================================


def _run_thread_listing(client, list_kwargs: dict, args, summary_fn):
    """Paginated thread listing shared by `inbox` and `search`.

    list_kwargs: filter args for threads.list (e.g. {"labelIds": ["INBOX"]} or {"q": "..."}).
    summary_fn: callable(result_size_estimate: int) -> (summary_line: str, total_count: int).
                Called once with the resultSizeEstimate observed in the first API response.
    """
    limit = max(1, args.limit)
    skip = max(0, args.skip or 0)
    page_token = args.cursor
    total_estimate = 0
    estimate_captured = False

    def threads_list(token, page_size):
        return (
            client.service.users()
            .threads()
            .list(
                userId="me",
                maxResults=min(page_size, 500),
                pageToken=token,
                **list_kwargs,
            )
            .execute()
        )

    # Walk skip pages when --skip is given without --cursor.
    if page_token is None and skip > 0:
        remaining = skip
        while remaining > 0:
            try:
                step = threads_list(page_token, remaining)
            except Exception as e:
                output_error(str(e), client.account_email)
                return
            if not estimate_captured:
                total_estimate = step.get("resultSizeEstimate", 0)
                estimate_captured = True
            walked = len(step.get("threads", []))
            page_token = step.get("nextPageToken")
            if walked == 0 or page_token is None:
                break
            remaining -= walked

    # Fetch up to `limit` threads, paginating through 500-cap responses if needed.
    threads = []
    next_token = page_token
    while len(threads) < limit:
        try:
            res = threads_list(next_token, limit - len(threads))
        except Exception as e:
            output_error(str(e), client.account_email)
            return
        if not estimate_captured:
            total_estimate = res.get("resultSizeEstimate", 0)
            estimate_captured = True
        threads.extend(res.get("threads", []))
        next_token = res.get("nextPageToken")
        if not next_token:
            break
    threads = threads[:limit]

    summary_line, total_count = summary_fn(total_estimate)
    header_line = f"account: {client.account_email} ({client.account_name})"

    total_pages = max(1, (total_count + limit - 1) // limit) if total_count else 1
    current_page = (skip // limit) + 1

    footer_lines = ["", f"page: {current_page}/{total_pages}"]
    if next_token:
        footer_lines.append(f"next_cursor: {next_token}")

    if not threads:
        print("\n".join([header_line, summary_line] + footer_lines))
        return

    label_map = _build_user_label_map(client)
    thread_data = {}
    chunk_size = 50
    for i in range(0, len(threads), chunk_size):
        chunk = threads[i : i + chunk_size]
        batch = client.service.new_batch_http_request()

        def cb(rid, resp, exc):
            if not exc:
                thread_data[rid] = resp

        for t in chunk:
            batch.add(
                client.service.users()
                .threads()
                .get(
                    userId="me",
                    id=t["id"],
                    format="metadata",
                    metadataHeaders=["From", "To", "Subject", "Date", "Reply-To"],
                ),
                request_id=t["id"],
                callback=cb,
            )
        try:
            batch.execute()
        except Exception as e:
            output_error(f"Batch error: {e}", client.account_email)
            return

    blocks = [header_line, summary_line, ""]
    for t in threads:
        thread = thread_data.get(t["id"])
        if not thread:
            continue
        messages = thread.get("messages", [])
        if not messages:
            continue

        total = len(messages)
        unread = sum(1 for m in messages if "UNREAD" in m.get("labelIds", []))
        latest = messages[-1]
        headers = latest.get("payload", {}).get("headers", [])

        from_raw = get_header(headers, "from", "Unknown")
        from_email = parseaddr(from_raw)[1].lower()
        reply_to = get_header(headers, "reply-to")

        label_ids = set()
        for m in messages:
            label_ids.update(m.get("labelIds", []))
        user_labels = sorted(label_map[lid] for lid in label_ids if lid in label_map)
        labels_str = " ".join(f"[[{n}]]" for n in user_labels)

        blocks.append(f"id: {latest['id']} (thread_id: {thread['id']})")
        if total > 1:
            blocks.append(f"unread: {unread}/{total}")
        elif unread:
            blocks.append("unread: yes")
        blocks.append(f"date: {format_date(get_header(headers, 'date'))}")
        blocks.append(f"from: {from_raw}")
        blocks.append(f"to: {get_header(headers, 'to')}")
        if reply_to and parseaddr(reply_to)[1].lower() != from_email:
            blocks.append(f"reply_to: {reply_to}")
        blocks.append(f"subject: {get_header(headers, 'subject', 'No Subject')}")
        if labels_str:
            blocks.append(f"labels: {labels_str}")
        blocks.append(f"snippet: {clean_snippet(latest.get('snippet', ''))}")
        blocks.append("")

    print("\n".join(blocks).rstrip() + "\n\n" + "\n".join(footer_lines[1:]))


def cmd_inbox(args):
    """Gmail-client-style inbox view: threads sorted by recency, with unread/label info."""
    client = get_client(args.account)
    try:
        inbox_label = client.service.users().labels().get(userId="me", id="INBOX").execute()
    except Exception as e:
        output_error(f"Could not fetch inbox metadata: {e}", client.account_email)
        return
    total = inbox_label.get("threadsTotal", 0)
    unread = inbox_label.get("threadsUnread", 0)
    _run_thread_listing(
        client,
        list_kwargs={"labelIds": ["INBOX"]},
        args=args,
        summary_fn=lambda _est: (f"inbox: {total} threads, {unread} unread", total),
    )


def cmd_search(args):
    """Run a Gmail search and render results with the inbox display contract."""
    client = get_client(args.account)
    q = args.query
    _run_thread_listing(
        client,
        list_kwargs={"q": q},
        args=args,
        summary_fn=lambda est: (f"search {q!r}: ~{est} matches", est),
    )


def cmd_read(args):
    """Read full email content."""
    client = get_client(args.account)

    try:
        msg = (
            client.service.users()
            .messages()
            .get(userId="me", id=args.id, format="full")
            .execute()
        )
    except Exception as e:
        output_error(str(e), client.account_email)
        return

    label_map = _build_user_label_map(client)
    print(_format_message_detail(client, msg, label_map))


def _format_message_detail(client, msg: dict, label_map: dict) -> str:
    """Render a single message with full body, attachments, and user labels."""
    payload = msg.get("payload", {})
    headers = payload.get("headers", [])

    from_raw = get_header(headers, "from", "Unknown")
    from_email = parseaddr(from_raw)[1].lower()
    reply_to = get_header(headers, "reply-to")
    cc = get_header(headers, "cc")
    bcc = get_header(headers, "bcc")

    label_ids = msg.get("labelIds", [])
    labels_str = format_user_labels(label_ids, label_map)
    attachments = collect_attachments(payload)
    body = gfm_quote_body(clean_body(extract_body(payload))) or clean_snippet(msg.get("snippet", ""))

    lines = [
        f"account: {client.account_email} ({client.account_name})",
        f"id: {msg['id']} (thread_id: {msg.get('threadId', '')})",
        f"date: {format_date(get_header(headers, 'date'))}",
        f"from: {from_raw}",
        f"to: {get_header(headers, 'to')}",
    ]
    if cc:
        lines.append(f"cc: {cc}")
    if bcc:
        lines.append(f"bcc: {bcc}")
    if reply_to and parseaddr(reply_to)[1].lower() != from_email:
        lines.append(f"reply_to: {reply_to}")
    lines.append(f"subject: {get_header(headers, 'subject', 'No Subject')}")
    if "UNREAD" in label_ids:
        lines.append("unread: yes")
    if labels_str:
        lines.append(f"labels: {labels_str}")
    if attachments:
        atts = ", ".join(f"{a['filename']} ({fmt_size(a['size'])})" for a in attachments)
        lines.append(f"attachments: {atts}")
    lines.append("")
    lines.append(body.rstrip())
    return "\n".join(lines)


def cmd_thread(args):
    """Show a full thread with bodies. Paginates with --limit/--skip from newest."""
    client = get_client(args.account)

    try:
        thread = (
            client.service.users()
            .threads()
            .get(userId="me", id=args.id, format="full")
            .execute()
        )
    except Exception as e:
        output_error(str(e), client.account_email)
        return

    messages = thread.get("messages", [])
    total = len(messages)
    if total == 0:
        print(f"account: {client.account_email} ({client.account_name})\nthread_id: {args.id}\nmessages: 0")
        return

    limit = max(1, args.limit)
    skip = max(0, args.skip or 0)
    end = max(0, total - skip)
    start = max(0, end - limit)
    sliced = messages[start:end]
    positions = list(range(start + 1, end + 1))

    label_map = _build_user_label_map(client)
    all_label_ids = set()
    for m in messages:
        all_label_ids.update(m.get("labelIds", []))
    thread_labels = format_user_labels(all_label_ids, label_map)

    unread_count = sum(1 for m in messages if "UNREAD" in m.get("labelIds", []))
    first_headers = messages[0].get("payload", {}).get("headers", [])
    thread_subject = get_header(first_headers, "subject", "No Subject")

    header_lines = [
        f"account: {client.account_email} ({client.account_name})",
        f"thread_id: {args.id}",
        f"messages: {total} ({unread_count} unread)",
        f"subject: {thread_subject}",
    ]
    if thread_labels:
        header_lines.append(f"labels: {thread_labels}")

    footer_line = f"shown: {start + 1}-{end}/{total}" if sliced else f"shown: 0/{total}"

    if not sliced:
        print("\n".join(header_lines + ["", footer_line]))
        return

    blocks = ["\n".join(header_lines)]
    for pos, msg in zip(positions, sliced):
        payload = msg.get("payload", {})
        headers = payload.get("headers", [])
        from_raw = get_header(headers, "from", "Unknown")
        from_email = parseaddr(from_raw)[1].lower()
        reply_to = get_header(headers, "reply-to")
        label_ids = msg.get("labelIds", [])

        msg_lines = [f"--- message {pos}/{total} ---"]
        msg_lines.append(f"id: {msg['id']}")
        msg_lines.append(f"date: {format_date(get_header(headers, 'date'))}")
        msg_lines.append(f"from: {from_raw}")
        msg_lines.append(f"to: {get_header(headers, 'to')}")
        cc = get_header(headers, "cc")
        if cc:
            msg_lines.append(f"cc: {cc}")
        if reply_to and parseaddr(reply_to)[1].lower() != from_email:
            msg_lines.append(f"reply_to: {reply_to}")
        if "UNREAD" in label_ids:
            msg_lines.append("unread: yes")
        attachments = collect_attachments(payload)
        if attachments:
            atts = ", ".join(f"{a['filename']} ({fmt_size(a['size'])})" for a in attachments)
            msg_lines.append(f"attachments: {atts}")
        body = gfm_quote_body(clean_body(extract_body(payload))) or clean_snippet(msg.get("snippet", ""))
        msg_lines.append("")
        msg_lines.append(body.rstrip())
        blocks.append("\n".join(msg_lines))

    print("\n\n".join(blocks) + "\n\n" + footer_line)


def cmd_trash(args):
    """Move emails to trash."""
    client = get_client(args.account)
    ids = parse_ids(args.ids)
    result = batch_message_operation(client, ids, "trash")
    print(json.dumps(result))


def cmd_untrash(args):
    """Restore emails from trash."""
    client = get_client(args.account)
    ids = parse_ids(args.ids)
    result = batch_message_operation(client, ids, "untrash")
    print(json.dumps(result))


def cmd_archive(args):
    """Archive emails (remove INBOX label)."""
    client = get_client(args.account)
    ids = parse_ids(args.ids)
    if not ids:
        output_success({"count": 0, "status": "skipped"}, client.account_email)
        return

    remove_labels = ["INBOX"]
    if args.read:
        remove_labels.append("UNREAD")

    body = {"ids": ids, "removeLabelIds": remove_labels}

    try:
        client.service.users().messages().batchModify(userId="me", body=body).execute()
        data = {"count": len(ids), "action": "archive"}
        if args.read:
            data["marked_read"] = True
        output_success(data, client.account_email)
    except Exception as e:
        output_error(str(e), client.account_email)


def cmd_move(args):
    """Move emails to a label with optional mark-as-read."""
    client = get_client(args.account)
    ids = parse_ids(args.ids)
    if not ids:
        print(json.dumps({"status": "skipped", "count": 0, "account": client.account_email}))
        return

    # Find label (only create if --create flag is set)
    label_id = ensure_label(client, args.label, create=args.create)
    if not label_id:
        output_error(
            f"Label not found: '{args.label}'. Use --create to create it, or check existing labels with 'labels list'.",
            client.account_email
        )
        return

    # Build modification body
    add_labels = [label_id]
    remove_labels = ["INBOX"]

    if args.read:
        remove_labels.append("UNREAD")

    body = {"ids": ids, "addLabelIds": add_labels, "removeLabelIds": remove_labels}

    try:
        client.service.users().messages().batchModify(userId="me", body=body).execute()
        data = {"count": len(ids), "label": args.label}
        if args.read:
            data["marked_read"] = True
        output_success(data, client.account_email)
    except Exception as e:
        output_error(str(e), client.account_email)


# =============================================================================
# Maintenance Commands
# =============================================================================

def cmd_summary(args):
    """Get email content from a label for summarization."""
    client = get_client(args.account)

    # Find label ID using unified lookup
    label_id, _, _ = resolve_label(client, args.label)
    if not label_id:
        output_error(f"Label '{args.label}' not found", client.account_email)
        return

    resp = (
        client.service.users()
        .messages()
        .list(userId="me", labelIds=[label_id], maxResults=args.limit)
        .execute()
    )
    msgs = resp.get("messages", [])

    if not msgs:
        print(json.dumps({"emails": [], "count": 0, "account": client.account_email}))
        return

    batch = client.service.new_batch_http_request()
    batch_resp = {}

    def cb(rid, resp, exc):
        if not exc:
            batch_resp[rid] = resp

    for msg in msgs:
        batch.add(
            client.service.users()
            .messages()
            .get(userId="me", id=msg["id"], format="full"),
            request_id=msg["id"],
            callback=cb,
        )

    batch.execute()
    output = []

    for mid, data in batch_resp.items():
        payload = data.get("payload", {})
        headers = payload.get("headers", [])

        subject = get_header(headers, "subject", "No Subject")
        sender = get_header(headers, "from", "Unknown")
        date = get_header(headers, "date")

        body = ""
        if "parts" in payload:
            for part in payload["parts"]:
                if part["mimeType"] == "text/plain":
                    data_enc = part["body"].get("data", "")
                    if data_enc:
                        body = base64.urlsafe_b64decode(data_enc).decode("utf-8", errors="replace")
                        break
        elif "body" in payload:
            data_enc = payload["body"].get("data", "")
            if data_enc:
                body = base64.urlsafe_b64decode(data_enc).decode("utf-8", errors="replace")

        if not body:
            body = data.get("snippet", "")

        output.append({
            "id": mid,
            "subject": subject,
            "from": sender,
            "date": date,
            "body": body[:2000],
        })

    print(json.dumps({"emails": output, "count": len(output), "account": client.account_email}, indent=2))


def cmd_cleanup(args):
    """Delete emails older than N days from a label."""
    client = get_client(args.account)

    cutoff_date = datetime.now() - timedelta(days=args.days)
    date_query = cutoff_date.strftime("%Y/%m/%d")
    # Quote label name if it contains spaces or special characters
    label_query = f'"{args.label}"' if " " in args.label else args.label
    query = f"label:{label_query} before:{date_query}"

    print(f"[{client.account_email}] Searching for emails in '{args.label}' before {date_query}...", file=sys.stderr)

    msgs_to_trash = []
    page_token = None

    while True:
        resp = (
            client.service.users()
            .messages()
            .list(userId="me", q=query, pageToken=page_token)
            .execute()
        )
        msgs = resp.get("messages", [])
        if msgs:
            msgs_to_trash.extend([m["id"] for m in msgs])

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    if not msgs_to_trash:
        print(json.dumps({"status": "success", "count": 0, "message": "No old emails found", "account": client.account_email}))
        return

    print(f"[{client.account_email}] Trashing {len(msgs_to_trash)} emails...", file=sys.stderr)

    batch = client.service.new_batch_http_request()
    count = 0
    total = len(msgs_to_trash)

    for mid in msgs_to_trash:
        batch.add(client.service.users().messages().trash(userId="me", id=mid))
        count += 1
        if count % 50 == 0 or count == total:
            batch.execute()
            batch = client.service.new_batch_http_request()

    print(json.dumps({"status": "success", "count": total, "account": client.account_email}))


# =============================================================================
# Label Management
# =============================================================================

def _list_labels(client):
    """Fetch labels from Gmail. Returns a list of label dicts (id, name, type, ...)."""
    results = client.service.users().labels().list(userId="me").execute()
    return results.get("labels", [])


def _build_user_label_map(client):
    """Map {label_id: label_name} for user-created labels (skips system labels)."""
    return {
        l["id"]: l["name"]
        for l in _list_labels(client)
        if l.get("type") == "user"
    }


def _build_full_label_map(client):
    """Map {label_id: (name, type)} for all labels including system ones."""
    return {
        l["id"]: (l.get("name", l["id"]), l.get("type", "user"))
        for l in _list_labels(client)
    }


def _label_display(label_id: str, label_map: dict) -> str:
    """Render a label reference: [[name]] for user labels, raw NAME for system."""
    if label_id not in label_map:
        return f"<{label_id}>"
    name, ltype = label_map[label_id]
    return f"[[{name}]]" if ltype == "user" else name


def _format_filter(filt: dict, label_map: dict) -> str:
    """Render a Gmail filter as a text block with `if/then` lines."""
    lines = [f"filter_id: {filt.get('id', '?')}"]
    criteria = filt.get("criteria", {})
    action = filt.get("action", {})

    for key in ("from", "to", "subject", "query"):
        val = criteria.get(key)
        if val:
            lines.append(f"  if {key}: {val}")
    if criteria.get("hasAttachment"):
        lines.append("  if has_attachment")
    if criteria.get("excludeChats"):
        lines.append("  if exclude_chats")
    if criteria.get("size"):
        comp = criteria.get("sizeComparison", "")
        lines.append(f"  if size {comp} {criteria['size']}")

    add_ids = list(action.get("addLabelIds", []))
    remove_ids = list(action.get("removeLabelIds", []))

    # Translate common system-label actions into verbs.
    verbs_remove = {"INBOX": "archive", "UNREAD": "mark_read"}
    verbs_add = {"STARRED": "star", "IMPORTANT": "mark_important", "TRASH": "trash", "SPAM": "spam"}
    for sys_id, verb in verbs_remove.items():
        if sys_id in remove_ids:
            lines.append(f"  then {verb}")
            remove_ids.remove(sys_id)
    for sys_id, verb in verbs_add.items():
        if sys_id in add_ids:
            lines.append(f"  then {verb}")
            add_ids.remove(sys_id)

    for lid in add_ids:
        lines.append(f"  then label: {_label_display(lid, label_map)}")
    for lid in remove_ids:
        lines.append(f"  then remove_label: {_label_display(lid, label_map)}")
    if action.get("forward"):
        lines.append(f"  then forward: {action['forward']}")

    return "\n".join(lines)


def cmd_labels_list(args):
    """List Gmail labels grouped by system/user with message counts."""
    client = get_client(args.account)
    try:
        labels = _list_labels(client)
    except Exception as e:
        output_error(str(e), client.account_email)
        return

    n_total = len(labels)
    n_user = sum(1 for l in labels if l.get("type") == "user")
    n_system = n_total - n_user

    system_labels = [l for l in labels if l.get("type") == "system"]
    user_labels = sorted(
        [l for l in labels if l.get("type") == "user"],
        key=lambda x: (x.get("name") or "").lower(),
    )

    def fmt_count(l):
        total = l.get("messagesTotal")
        if total is None:
            return ""
        unread = l.get("messagesUnread", 0)
        return f" ({total} msgs, {unread} unread)" if unread else f" ({total} msgs)"

    lines = [
        f"account: {client.account_email} ({client.account_name})",
        f"labels: {n_total} total ({n_system} system, {n_user} user)",
    ]
    if system_labels:
        lines.append("")
        lines.append("system:")
        for l in system_labels:
            lines.append(f"- {l.get('name', l['id'])}{fmt_count(l)}")
    if user_labels:
        lines.append("")
        lines.append("user:")
        for l in user_labels:
            name = l.get("name", l["id"])
            indent = "  " * name.count("/")
            lines.append(f"{indent}- [[{name}]]{fmt_count(l)}")

    print("\n".join(lines))


def cmd_labels_create(args):
    """Create a new Gmail label."""
    client = get_client(args.account)
    try:
        result = client.service.users().labels().create(
            userId="me",
            body={
                "name": args.name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        ).execute()
    except Exception as e:
        output_error(str(e), client.account_email)
        return

    print(f"account: {client.account_email} ({client.account_name})")
    print(f"created label: [[{result.get('name', args.name)}]]")
    print(f"id: {result.get('id')}")


def cmd_labels_delete(args):
    """Delete a Gmail label by name or ID."""
    client = get_client(args.account)
    try:
        label_id, label_name, label_type = resolve_label(client, args.name_or_id)
    except Exception as e:
        output_error(str(e), client.account_email)
        return

    if not label_id:
        output_error(f"Label not found: {args.name_or_id}", client.account_email)
        return
    if label_type == "system":
        output_error(f"Cannot delete system label: {label_name}", client.account_email)
        return

    try:
        client.service.users().labels().delete(userId="me", id=label_id).execute()
    except Exception as e:
        output_error(str(e), client.account_email)
        return

    print(f"account: {client.account_email} ({client.account_name})")
    print(f"deleted label: [[{label_name}]]")


def cmd_labels_rename(args):
    """Rename a Gmail label."""
    client = get_client(args.account)
    try:
        label_id, old_name, label_type = resolve_label(client, args.old_name)
    except Exception as e:
        output_error(str(e), client.account_email)
        return

    if not label_id:
        output_error(f"Label not found: {args.old_name}", client.account_email)
        return
    if label_type == "system":
        output_error(f"Cannot rename system label: {old_name}", client.account_email)
        return

    try:
        result = client.service.users().labels().patch(
            userId="me", id=label_id, body={"name": args.new_name}
        ).execute()
    except Exception as e:
        output_error(str(e), client.account_email)
        return

    print(f"account: {client.account_email} ({client.account_name})")
    print(f"renamed label: [[{old_name}]] -> [[{result.get('name', args.new_name)}]]")


# =============================================================================
# Filter Management
# =============================================================================

def cmd_filters_list(args):
    """List all Gmail filters as if/then blocks."""
    client = get_client(args.account)
    try:
        results = client.service.users().settings().filters().list(userId="me").execute()
    except Exception as e:
        output_error(str(e), client.account_email)
        return

    filters = results.get("filter", [])
    header_line = f"account: {client.account_email} ({client.account_name})"
    summary_line = f"filters: {len(filters)} total"

    if not filters:
        print(f"{header_line}\n{summary_line}")
        return

    label_map = _build_full_label_map(client)
    blocks = [f"{header_line}\n{summary_line}"]
    for f in filters:
        blocks.append(_format_filter(f, label_map))
    print("\n\n".join(blocks))


def cmd_filters_add(args):
    """Add a new Gmail filter."""
    client = get_client(args.account)

    criteria = {}
    if args.sender:
        criteria["from"] = args.sender
    if args.to:
        criteria["to"] = args.to
    if args.subject:
        criteria["subject"] = args.subject
    if args.query:
        criteria["query"] = args.query
    if args.has_attachment:
        criteria["hasAttachment"] = True

    if not criteria:
        output_error("At least one criteria required", client.account_email)
        return

    action = {}
    if args.add_label:
        label_id = ensure_label(client, args.add_label, create=True)
        if not label_id:
            output_error(f"Could not find or create label: {args.add_label}", client.account_email)
            return
        action["addLabelIds"] = [label_id]
    if args.archive:
        action["removeLabelIds"] = action.get("removeLabelIds", []) + ["INBOX"]
    if args.mark_read:
        action["removeLabelIds"] = action.get("removeLabelIds", []) + ["UNREAD"]
    if args.trash:
        action["addLabelIds"] = action.get("addLabelIds", []) + ["TRASH"]
    if args.star:
        action["addLabelIds"] = action.get("addLabelIds", []) + ["STARRED"]
    if args.forward:
        action["forward"] = args.forward

    if not action:
        output_error("At least one action required", client.account_email)
        return

    try:
        result = client.service.users().settings().filters().create(
            userId="me", body={"criteria": criteria, "action": action}
        ).execute()
    except Exception as e:
        output_error(str(e), client.account_email)
        return

    label_map = _build_full_label_map(client)
    print(f"account: {client.account_email} ({client.account_name})")
    print("created filter:")
    print(_format_filter(result, label_map))


def cmd_filters_delete(args):
    """Delete a Gmail filter by ID."""
    client = get_client(args.account)
    try:
        client.service.users().settings().filters().delete(
            userId="me", id=args.id
        ).execute()
    except Exception as e:
        output_error(str(e), client.account_email)
        return

    print(f"account: {client.account_email} ({client.account_name})")
    print(f"deleted filter: {args.id}")


# =============================================================================
# Attachment Operations
# =============================================================================

def cmd_attachments(args):
    """List attachments in an email."""
    client = get_client(args.account)

    try:
        msg = client.service.users().messages().get(
            userId="me", id=args.id, format="full"
        ).execute()

        payload = msg.get("payload", {})
        attachments = []

        def find_attachments(parts):
            for part in parts:
                filename = part.get("filename", "")
                if filename and part.get("body", {}).get("attachmentId"):
                    attachments.append({
                        "filename": filename,
                        "mimeType": part.get("mimeType", ""),
                        "attachmentId": part["body"]["attachmentId"],
                        "size": part.get("body", {}).get("size", 0)
                    })
                if "parts" in part:
                    find_attachments(part["parts"])

        if "parts" in payload:
            find_attachments(payload["parts"])

        output_success({
            "message_id": args.id,
            "attachments": attachments,
            "count": len(attachments)
        }, client.account_email, indent=2)

    except Exception as e:
        output_error(str(e), client.account_email)


def cmd_download(args):
    """Download attachments from an email."""
    client = get_client(args.account)

    # Ensure output directory exists
    output_dir = args.output if args.output else "."
    os.makedirs(output_dir, exist_ok=True)

    try:
        msg = client.service.users().messages().get(
            userId="me", id=args.id, format="full"
        ).execute()

        payload = msg.get("payload", {})
        downloaded = []

        def download_parts(parts):
            for part in parts:
                filename = part.get("filename", "")
                attachment_id = part.get("body", {}).get("attachmentId")

                if filename and attachment_id:
                    # Apply filename filter if specified
                    if args.filename and args.filename.lower() not in filename.lower():
                        continue

                    try:
                        attachment = client.service.users().messages().attachments().get(
                            userId="me", messageId=args.id, id=attachment_id
                        ).execute()

                        data = attachment.get("data", "")
                        if data:
                            file_data = base64.urlsafe_b64decode(data)

                            # Sanitize filename
                            safe_filename = filename.replace("/", "_").replace("\\", "_")

                            # Add prefix if specified
                            if args.prefix:
                                safe_filename = f"{args.prefix}_{safe_filename}"

                            filepath = os.path.join(output_dir, safe_filename)

                            # Handle duplicate filenames
                            base, ext = os.path.splitext(filepath)
                            counter = 1
                            while os.path.exists(filepath):
                                filepath = f"{base}_{counter}{ext}"
                                counter += 1

                            with open(filepath, "wb") as f:
                                f.write(file_data)

                            downloaded.append({
                                "filename": filename,
                                "saved_as": filepath,
                                "size": len(file_data)
                            })
                    except Exception as e:
                        downloaded.append({
                            "filename": filename,
                            "error": str(e)
                        })

                if "parts" in part:
                    download_parts(part["parts"])

        if "parts" in payload:
            download_parts(payload["parts"])

        output_success({
            "message_id": args.id,
            "downloaded": downloaded,
            "count": len([d for d in downloaded if "saved_as" in d]),
            "output_dir": output_dir
        }, client.account_email, indent=2)

    except Exception as e:
        output_error(str(e), client.account_email)


def cmd_search_download(args):
    """Search emails and download attachments matching criteria."""
    client = get_client(args.account)

    # Ensure output directory exists
    output_dir = args.output if args.output else "."
    os.makedirs(output_dir, exist_ok=True)

    try:
        # Search for emails
        response = client.service.users().messages().list(
            userId="me", q=args.query, maxResults=args.limit
        ).execute()

        messages = response.get("messages", [])
        all_downloaded = []
        emails_with_attachments = []

        for msg_info in messages:
            msg_id = msg_info["id"]

            msg = client.service.users().messages().get(
                userId="me", id=msg_id, format="full"
            ).execute()

            payload = msg.get("payload", {})
            headers = payload.get("headers", [])

            subject = next(
                (h["value"] for h in headers if h["name"].lower() == "subject"),
                "No Subject"
            )
            sender = next(
                (h["value"] for h in headers if h["name"].lower() == "from"),
                "Unknown"
            )
            date = next(
                (h["value"] for h in headers if h["name"].lower() == "date"),
                ""
            )

            # Parse year from date
            year = None
            try:
                year_match = re.search(r'\b(20\d{2})\b', date)
                if year_match:
                    year = int(year_match.group(1))
            except Exception:
                pass  # Year parsing is optional, continue without it

            def download_parts(parts):
                downloaded = []
                for part in parts:
                    filename = part.get("filename", "")
                    attachment_id = part.get("body", {}).get("attachmentId")

                    if filename and attachment_id:
                        try:
                            attachment = client.service.users().messages().attachments().get(
                                userId="me", messageId=msg_id, id=attachment_id
                            ).execute()

                            data = attachment.get("data", "")
                            if data:
                                file_data = base64.urlsafe_b64decode(data)

                                # Sanitize filename with account prefix
                                safe_filename = f"{client.account_name}_{filename}".replace("/", "_").replace("\\", "_")
                                filepath = os.path.join(output_dir, safe_filename)

                                # Handle duplicate filenames
                                base, ext = os.path.splitext(filepath)
                                counter = 1
                                while os.path.exists(filepath):
                                    filepath = f"{base}_{counter}{ext}"
                                    counter += 1

                                with open(filepath, "wb") as f:
                                    f.write(file_data)

                                downloaded.append({
                                    "filename": filename,
                                    "saved_as": filepath,
                                    "size": len(file_data),
                                    "email_subject": subject,
                                    "email_date": date,
                                    "year": year
                                })
                        except Exception as e:
                            downloaded.append({
                                "filename": filename,
                                "error": str(e)
                            })

                    if "parts" in part:
                        downloaded.extend(download_parts(part["parts"]))
                return downloaded

            if "parts" in payload:
                downloaded = download_parts(payload["parts"])
                if downloaded:
                    all_downloaded.extend(downloaded)
                    emails_with_attachments.append({
                        "id": msg_id,
                        "subject": subject,
                        "from": sender,
                        "date": date,
                        "year": year,
                        "attachments": [d["filename"] for d in downloaded if "filename" in d]
                    })

        output_success({
            "query": args.query,
            "emails_searched": len(messages),
            "emails_with_attachments": len(emails_with_attachments),
            "total_downloaded": len([d for d in all_downloaded if "saved_as" in d]),
            "output_dir": output_dir,
            "downloaded_files": all_downloaded,
            "emails": emails_with_attachments
        }, client.account_email, indent=2)

    except Exception as e:
        output_error(str(e), client.account_email)


# =============================================================================
# Send Email
# =============================================================================

def cmd_send(args):
    """Send an email."""
    client = get_client(args.account)

    try:
        # Create message
        if args.attachment:
            message = MIMEMultipart()
            message.attach(MIMEText(args.body, "plain"))

            # Attach files
            for filepath in args.attachment:
                if os.path.exists(filepath):
                    filename = os.path.basename(filepath)
                    mime_type, _ = mimetypes.guess_type(filepath)
                    if mime_type is None:
                        mime_type = "application/octet-stream"

                    main_type, sub_type = mime_type.split("/", 1)

                    with open(filepath, "rb") as f:
                        attachment = MIMEBase(main_type, sub_type)
                        attachment.set_payload(f.read())

                    encoders.encode_base64(attachment)
                    attachment.add_header(
                        "Content-Disposition",
                        "attachment",
                        filename=filename
                    )
                    message.attach(attachment)
                else:
                    output_error(f"Attachment not found: {filepath}", client.account_email)
                    return
        else:
            message = MIMEText(args.body, "plain")

        message["to"] = args.to
        message["subject"] = args.subject

        if args.cc:
            message["cc"] = args.cc
        if args.bcc:
            message["bcc"] = args.bcc
        if args.reply_to:
            message["reply-to"] = args.reply_to

        # Encode and send
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8", errors="replace")

        result = client.service.users().messages().send(
            userId="me",
            body={"raw": raw}
        ).execute()

        output_success({
            "message_id": result.get("id"),
            "thread_id": result.get("threadId"),
            "to": args.to,
            "subject": args.subject
        }, client.account_email, indent=2)

    except Exception as e:
        output_error(str(e), client.account_email)


def cmd_reply(args):
    """Reply to an email."""
    client = get_client(args.account)

    try:
        # Get original message
        original = client.service.users().messages().get(
            userId="me", id=args.id, format="full"
        ).execute()

        payload = original.get("payload", {})
        headers = payload.get("headers", [])

        # Extract headers using helper
        original_subject = get_header(headers, "subject")
        original_from = get_header(headers, "from")
        reply_to = get_header(headers, "reply-to") or None
        recipient = args.to if args.to else (reply_to if reply_to else original_from)
        message_id = get_header(headers, "message-id")
        references = get_header(headers, "references")

        # Build reply subject
        reply_subject = original_subject
        if not reply_subject.lower().startswith("re:"):
            reply_subject = f"Re: {reply_subject}"

        # Create message
        message = MIMEText(args.body, "plain")
        message["to"] = recipient
        message["subject"] = reply_subject
        message["In-Reply-To"] = message_id
        message["References"] = f"{references} {message_id}".strip()

        if args.cc:
            message["cc"] = args.cc
        if args.bcc:
            message["bcc"] = args.bcc

        # Encode and send
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8", errors="replace")

        result = client.service.users().messages().send(
            userId="me",
            body={
                "raw": raw,
                "threadId": original.get("threadId")
            }
        ).execute()

        output_success({
            "message_id": result.get("id"),
            "thread_id": result.get("threadId"),
            "to": recipient,
            "subject": reply_subject
        }, client.account_email, indent=2)

    except Exception as e:
        output_error(str(e), client.account_email)


# =============================================================================
# Draft Operations
# =============================================================================

def cmd_draft(args):
    """Create a new draft email."""
    client = get_client(args.account)

    try:
        # Create message
        if args.attachment:
            message = MIMEMultipart()
            message.attach(MIMEText(args.body, "plain"))

            for filepath in args.attachment:
                if os.path.exists(filepath):
                    filename = os.path.basename(filepath)
                    mime_type, _ = mimetypes.guess_type(filepath)
                    if mime_type is None:
                        mime_type = "application/octet-stream"

                    main_type, sub_type = mime_type.split("/", 1)

                    with open(filepath, "rb") as f:
                        attachment = MIMEBase(main_type, sub_type)
                        attachment.set_payload(f.read())

                    encoders.encode_base64(attachment)
                    attachment.add_header(
                        "Content-Disposition",
                        "attachment",
                        filename=filename
                    )
                    message.attach(attachment)
                else:
                    output_error(f"Attachment not found: {filepath}", client.account_email)
                    return
        else:
            message = MIMEText(args.body, "plain")

        message["to"] = args.to
        message["subject"] = args.subject

        if args.cc:
            message["cc"] = args.cc
        if args.bcc:
            message["bcc"] = args.bcc

        # Encode and create draft
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8", errors="replace")

        result = client.service.users().drafts().create(
            userId="me",
            body={"message": {"raw": raw}}
        ).execute()

        output_success({
            "draft_id": result.get("id"),
            "message_id": result.get("message", {}).get("id"),
            "to": args.to,
            "subject": args.subject
        }, client.account_email, indent=2)

    except Exception as e:
        output_error(str(e), client.account_email)


def cmd_draft_reply(args):
    """Create a draft reply to an existing email."""
    client = get_client(args.account)

    try:
        # Get original message
        original = client.service.users().messages().get(
            userId="me", id=args.id, format="full"
        ).execute()

        payload = original.get("payload", {})
        headers = payload.get("headers", [])

        # Extract headers using helper
        original_subject = get_header(headers, "subject")
        original_from = get_header(headers, "from")
        reply_to = get_header(headers, "reply-to") or None
        recipient = reply_to if reply_to else original_from
        message_id = get_header(headers, "message-id")
        references = get_header(headers, "references")

        # Build reply subject
        reply_subject = original_subject
        if not reply_subject.lower().startswith("re:"):
            reply_subject = f"Re: {reply_subject}"

        # Create message
        message = MIMEText(args.body, "plain")
        message["to"] = recipient
        message["subject"] = reply_subject
        message["In-Reply-To"] = message_id
        message["References"] = f"{references} {message_id}".strip()

        if args.cc:
            message["cc"] = args.cc

        # Encode and create draft
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8", errors="replace")

        result = client.service.users().drafts().create(
            userId="me",
            body={
                "message": {
                    "raw": raw,
                    "threadId": original.get("threadId")
                }
            }
        ).execute()

        output_success({
            "draft_id": result.get("id"),
            "message_id": result.get("message", {}).get("id"),
            "thread_id": original.get("threadId"),
            "to": recipient,
            "subject": reply_subject
        }, client.account_email, indent=2)

    except Exception as e:
        output_error(str(e), client.account_email)


def cmd_drafts_list(args):
    """List all drafts."""
    client = get_client(args.account)

    try:
        result = client.service.users().drafts().list(
            userId="me", maxResults=args.limit
        ).execute()

        drafts = result.get("drafts", [])

        if not drafts:
            print(json.dumps({"drafts": [], "count": 0, "account": client.account_email}))
            return

        output = []
        for draft in drafts:
            draft_id = draft.get("id")
            msg = draft.get("message", {})
            msg_id = msg.get("id")

            # Get full message details
            try:
                full_msg = client.service.users().messages().get(
                    userId="me", id=msg_id, format="metadata",
                    metadataHeaders=["Subject", "To", "Date"]
                ).execute()

                headers = full_msg.get("payload", {}).get("headers", [])
                output.append({
                    "draft_id": draft_id,
                    "message_id": msg_id,
                    "subject": get_header(headers, "subject", "No Subject"),
                    "to": get_header(headers, "to"),
                    "date": get_header(headers, "date")
                })
            except Exception:
                # Could not fetch full message details, return basic info
                output.append({
                    "draft_id": draft_id,
                    "message_id": msg_id
                })

        print(json.dumps({
            "drafts": output,
            "count": len(output),
            "account": client.account_email
        }, indent=2))

    except Exception as e:
        output_error(str(e), client.account_email)


def cmd_draft_delete(args):
    """Delete a draft."""
    client = get_client(args.account)

    try:
        client.service.users().drafts().delete(
            userId="me", id=args.id
        ).execute()

        output_success({"deleted_draft_id": args.id}, client.account_email)

    except Exception as e:
        output_error(str(e), client.account_email)


def cmd_draft_send(args):
    """Send an existing draft."""
    client = get_client(args.account)

    try:
        result = client.service.users().drafts().send(
            userId="me", body={"id": args.id}
        ).execute()

        output_success({
            "message_id": result.get("id"),
            "thread_id": result.get("threadId")
        }, client.account_email, indent=2)

    except Exception as e:
        output_error(str(e), client.account_email)


# =============================================================================
# Helpers
# =============================================================================

def output_error(message: str, account: str = None) -> None:
    """Unified error output format."""
    result = {"status": "error", "message": message}
    if account:
        result["account"] = account
    print(json.dumps(result))


def output_success(data: dict, account: str = None, indent: int = None) -> None:
    """Unified success output format."""
    # Remove 'status' from data if present to prevent override
    clean_data = {k: v for k, v in data.items() if k != "status"}
    result = {"status": "success", **clean_data}
    if account:
        result["account"] = account
    print(json.dumps(result, indent=indent))


def get_header(headers: list, name: str, default: str = "") -> str:
    """Extract a header value from headers list (case-insensitive)."""
    return next(
        (h["value"] for h in headers if h["name"].lower() == name.lower()),
        default
    )


# Zero-width, bidi-control, joiner, soft-hyphen, BOM — common noise in marketing snippets.
_INVISIBLE_CHARS_RE = re.compile(
    r"[\u00AD\u034F\u200B-\u200F\u202A-\u202E\u2060-\u2064\u206A-\u206F\uFEFF]"
)


def clean_snippet(s: str) -> str:
    """Strip invisible chars, decode HTML entities, collapse whitespace."""
    if not s:
        return ""
    s = html.unescape(s)
    s = _INVISIBLE_CHARS_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def clean_body(s: str) -> str:
    """Strip invisible chars and trailing-whitespace lines; collapse 2+ blank lines."""
    if not s:
        return ""
    s = _INVISIBLE_CHARS_RE.sub("", s)
    s = "\n".join(line.rstrip() for line in s.split("\n"))
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# Reply attribution markers — when a body inlines the previous message, lines after this
# get treated as a quoted block. Covers common English/Chinese/Forwarded-message styles.
_QUOTE_MARKER_RE = re.compile(
    r"^\s*(?:"
    r"On\b.{1,500}\bwrote\s*[:：]"
    r"|.{1,300}\b写道\s*[:：]"
    r"|-{2,}\s*Original Message\s*-{2,}"
    r"|-{2,}\s*Forwarded message\s*-{2,}"
    r")\s*$"
)


def gfm_quote_body(body: str) -> str:
    """Wrap inline reply quotes in GFM blockquote markers based on attribution lines."""
    if not body:
        return body
    lines = body.split("\n")
    out = []
    depth = 0
    for line in lines:
        if _QUOTE_MARKER_RE.match(line):
            prefix = "> " * depth
            out.append(prefix + line)
            depth += 1
            continue
        if depth > 0:
            stripped = line.lstrip()
            if not stripped:
                out.append(("> " * depth).rstrip())
            else:
                out.append("> " * depth + line)
        else:
            out.append(line)
    return "\n".join(out)


def extract_body(payload: dict) -> str:
    """Walk Gmail payload tree and return the first text/plain body (fallback: text/html stripped)."""
    if not payload:
        return ""

    mime = payload.get("mimeType", "")
    body = payload.get("body", {})

    if mime == "text/plain":
        data = body.get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    parts = payload.get("parts", [])
    for part in parts:
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    for part in parts:
        if part.get("mimeType", "").startswith("multipart/"):
            nested = extract_body(part)
            if nested:
                return nested

    # Last resort: html stripped to text
    def find_html(p):
        if p.get("mimeType") == "text/html":
            data = p.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        for sub in p.get("parts", []):
            r = find_html(sub)
            if r:
                return r
        return ""

    html_body = find_html(payload)
    if html_body:
        text = re.sub(r"<[^>]+>", " ", html_body)
        text = html.unescape(text)
        return re.sub(r"\s+", " ", text).strip()

    return ""


def collect_attachments(payload: dict, found: list = None) -> list:
    """Walk payload tree and collect attachment metadata."""
    if found is None:
        found = []
    if not payload:
        return found
    filename = payload.get("filename", "")
    body = payload.get("body", {})
    if filename and body.get("attachmentId"):
        found.append({
            "filename": filename,
            "size": body.get("size", 0),
            "mime": payload.get("mimeType", ""),
        })
    for part in payload.get("parts", []):
        collect_attachments(part, found)
    return found


def fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    for unit in ("KB", "MB", "GB"):
        n /= 1024
        if n < 1024:
            return f"{n:.1f}{unit}"
    return f"{n:.1f}TB"


def format_user_labels(label_ids, label_map: dict) -> str:
    names = sorted(label_map[lid] for lid in label_ids if lid in label_map)
    return " ".join(f"[[{n}]]" for n in names)


def _humanize_delta(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    if seconds < 0:
        seconds = -seconds
        suffix = "from now"
    else:
        suffix = "ago"
    if seconds < 60:
        return f"{seconds}s {suffix}"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m {suffix}"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h {suffix}"
    days = hours // 24
    if days < 30:
        return f"{days}d {suffix}"
    months = days // 30
    if months < 12:
        return f"{months}mo {suffix}"
    return f"{days // 365}y {suffix}"


def format_date(raw: str) -> str:
    """Render RFC 2822 date as 'ISO (relative)'. Falls back to raw on parse failure."""
    if not raw:
        return ""
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return raw
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    rel = _humanize_delta(datetime.now(timezone.utc) - dt)
    return f"{dt.isoformat()} ({rel})"


def parse_ids(ids_input):
    """Parse IDs from JSON array or comma-separated string."""
    if not ids_input:
        return []

    ids_input = ids_input.strip()

    # Try JSON array first
    if ids_input.startswith("["):
        try:
            parsed = json.loads(ids_input)
            # Filter empty strings and strip whitespace
            return [str(id_).strip() for id_ in parsed if str(id_).strip()]
        except json.JSONDecodeError as e:
            print(json.dumps({"status": "error", "message": f"Invalid JSON: {e}"}), file=sys.stderr)
            return []

    # Comma-separated: split, strip, and filter empty
    return [id_.strip() for id_ in ids_input.split(",") if id_.strip()]


def batch_message_operation(client, ids: list, operation: str) -> dict:
    """
    Execute batch operation on messages.

    Args:
        client: Gmail client
        ids: List of message IDs
        operation: 'trash' or 'untrash'

    Returns:
        Result dict with status, count, and account
    """
    if not ids:
        return {"status": "skipped", "count": 0, "account": client.account_email}

    batch = client.service.new_batch_http_request()
    method = getattr(client.service.users().messages(), operation)

    for i, mid in enumerate(ids, 1):
        batch.add(method(userId="me", id=mid))

        if i % 50 == 0 or i == len(ids):
            try:
                batch.execute()
                time.sleep(0.5)
                batch = client.service.new_batch_http_request()
            except Exception as e:
                return {"status": "error", "message": str(e), "account": client.account_email}

    return {"status": "success", "count": len(ids), "account": client.account_email}


def resolve_label(client, name_or_id):
    """
    Unified label lookup by ID or name (case-insensitive).
    Returns: (label_id, label_name, label_type) or (None, None, None)
    """
    labels = _list_labels(client)

    # First try exact ID match
    for label in labels:
        if label.get("id") == name_or_id:
            return label.get("id"), label.get("name"), label.get("type")

    # Then try case-insensitive name match
    needle = (name_or_id or "").lower()
    for label in labels:
        if (label.get("name") or "").lower() == needle:
            return label.get("id"), label.get("name"), label.get("type")

    return None, None, None


def ensure_label(client, label_name, create=False):
    """Find a label by name. If create=True, creates it if not found."""
    label_id, _, _ = resolve_label(client, label_name)

    if label_id:
        return label_id

    if not create:
        return None

    try:
        label_object = {
            "name": label_name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        }
        created_label = (
            client.service.users()
            .labels()
            .create(userId="me", body=label_object)
            .execute()
        )
        return created_label["id"]
    except Exception as e:
        output_error(f"Failed to create label: {str(e)}", client.account_email)
        return None


# =============================================================================
# CLI Setup
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Email CLI for email-copilot skill (multi-account)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Global account option
    parser.add_argument("-a", "--account", help="Account name (from config.toml). Uses default if not specified.")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # accounts
    p_accounts = subparsers.add_parser("accounts", help="List configured accounts")
    p_accounts.set_defaults(func=cmd_accounts)

    # inbox
    p_inbox = subparsers.add_parser("inbox", help="Inbox view (threads, unread counts, user labels)")
    p_inbox.add_argument("-n", "--limit", type=int, default=100, help="Threads per page (default: 100)")
    p_inbox.add_argument("--skip", type=int, default=0, help="Skip N threads (slow; walks pages)")
    p_inbox.add_argument("--cursor", help="Page token from a previous inbox call's next_cursor")
    p_inbox.set_defaults(func=cmd_inbox)

    # search
    p_search = subparsers.add_parser("search", help="Search threads with a Gmail query (same display as inbox)")
    p_search.add_argument("query", help="Gmail search query (e.g. 'from:luca', 'is:unread label:Newsletters')")
    p_search.add_argument("-n", "--limit", type=int, default=100, help="Threads per page (default: 100)")
    p_search.add_argument("--skip", type=int, default=0, help="Skip N threads (slow; walks pages)")
    p_search.add_argument("--cursor", help="Page token from a previous search call's next_cursor")
    p_search.set_defaults(func=cmd_search)

    # read
    p_read = subparsers.add_parser("read", help="Read full email content")
    p_read.add_argument("id", help="Email ID")
    p_read.set_defaults(func=cmd_read)

    # thread
    p_thread = subparsers.add_parser("thread", help="Show full thread with bodies (paginated)")
    p_thread.add_argument("id", help="Thread ID")
    p_thread.add_argument("-n", "--limit", type=int, default=20, help="Messages per page from newest (default: 20)")
    p_thread.add_argument("--skip", type=int, default=0, help="Skip N newest messages (deeper history)")
    p_thread.set_defaults(func=cmd_thread)

    # trash
    p_trash = subparsers.add_parser("trash", help="Move emails to trash")
    p_trash.add_argument("ids", help="Email IDs (JSON array or comma-separated)")
    p_trash.set_defaults(func=cmd_trash)

    # untrash
    p_untrash = subparsers.add_parser("untrash", help="Restore emails from trash")
    p_untrash.add_argument("ids", help="Email IDs (JSON array or comma-separated)")
    p_untrash.set_defaults(func=cmd_untrash)

    # archive
    p_archive = subparsers.add_parser("archive", help="Archive emails (remove INBOX label)")
    p_archive.add_argument("ids", help="Email IDs (JSON array or comma-separated)")
    p_archive.add_argument("-r", "--read", action="store_true", help="Also mark as read")
    p_archive.set_defaults(func=cmd_archive)

    # move
    p_move = subparsers.add_parser("move", help="Move emails to a label")
    p_move.add_argument("label", help="Target label name (must exist, use 'labels list' to check)")
    p_move.add_argument("ids", help="Email IDs (JSON array or comma-separated)")
    p_move.add_argument("-r", "--read", action="store_true", help="Also mark as read")
    p_move.add_argument("-c", "--create", action="store_true", help="Create label if it doesn't exist")
    p_move.set_defaults(func=cmd_move)

    # summary
    p_summary = subparsers.add_parser("summary", help="Get email content for summarization")
    p_summary.add_argument("label", help="Label name")
    p_summary.add_argument("-n", "--limit", type=int, default=20, help="Max emails")
    p_summary.set_defaults(func=cmd_summary)

    # cleanup
    p_cleanup = subparsers.add_parser("cleanup", help="Delete old emails from a label")
    p_cleanup.add_argument("label", help="Label name")
    p_cleanup.add_argument("-d", "--days", type=int, default=30, help="Days threshold")
    p_cleanup.set_defaults(func=cmd_cleanup)

    # labels
    p_labels = subparsers.add_parser("labels", help="Manage Gmail labels")
    labels_sub = p_labels.add_subparsers(dest="labels_cmd")

    p_labels_list = labels_sub.add_parser("list", help="List all labels")
    p_labels_list.set_defaults(func=cmd_labels_list)

    p_labels_create = labels_sub.add_parser("create", help="Create a new label")
    p_labels_create.add_argument("name", help="Label name")
    p_labels_create.set_defaults(func=cmd_labels_create)

    p_labels_delete = labels_sub.add_parser("delete", help="Delete a label")
    p_labels_delete.add_argument("name_or_id", help="Label name or ID")
    p_labels_delete.set_defaults(func=cmd_labels_delete)

    p_labels_rename = labels_sub.add_parser("rename", help="Rename a label")
    p_labels_rename.add_argument("old_name", help="Current label name")
    p_labels_rename.add_argument("new_name", help="New label name")
    p_labels_rename.set_defaults(func=cmd_labels_rename)

    # filters
    p_filters = subparsers.add_parser("filters", help="Manage Gmail filters")
    filters_sub = p_filters.add_subparsers(dest="filters_cmd")

    p_filters_list = filters_sub.add_parser("list", help="List all filters")
    p_filters_list.set_defaults(func=cmd_filters_list)

    p_filters_add = filters_sub.add_parser("add", help="Add a new filter")
    p_filters_add.add_argument("--from", dest="sender", help="Filter by sender")
    p_filters_add.add_argument("--to", help="Filter by recipient")
    p_filters_add.add_argument("--subject", help="Filter by subject")
    p_filters_add.add_argument("--query", help="Gmail search query")
    p_filters_add.add_argument("--has-attachment", action="store_true")
    p_filters_add.add_argument("--add-label", help="Add label")
    p_filters_add.add_argument("--archive", action="store_true")
    p_filters_add.add_argument("--mark-read", action="store_true")
    p_filters_add.add_argument("--trash", action="store_true")
    p_filters_add.add_argument("--star", action="store_true")
    p_filters_add.add_argument("--forward", help="Forward to email")
    p_filters_add.set_defaults(func=cmd_filters_add)

    p_filters_delete = filters_sub.add_parser("delete", help="Delete a filter")
    p_filters_delete.add_argument("id", help="Filter ID")
    p_filters_delete.set_defaults(func=cmd_filters_delete)

    # attachments - list attachments in an email
    p_attachments = subparsers.add_parser("attachments", help="List attachments in an email")
    p_attachments.add_argument("id", help="Email ID")
    p_attachments.set_defaults(func=cmd_attachments)

    # download - download attachments from an email
    p_download = subparsers.add_parser("download", help="Download attachments from an email")
    p_download.add_argument("id", help="Email ID")
    p_download.add_argument("-o", "--output", help="Output directory (default: current dir)")
    p_download.add_argument("-f", "--filename", help="Filter by filename (partial match)")
    p_download.add_argument("-p", "--prefix", help="Add prefix to saved filenames")
    p_download.set_defaults(func=cmd_download)

    # search-download - search and download attachments
    p_search_download = subparsers.add_parser("search-download", help="Search emails and download attachments")
    p_search_download.add_argument("-q", "--query", required=True, help="Gmail search query")
    p_search_download.add_argument("-o", "--output", help="Output directory (default: current dir)")
    p_search_download.add_argument("-n", "--limit", type=int, default=100, help="Max emails to search")
    p_search_download.set_defaults(func=cmd_search_download)

    # send - send an email
    p_send = subparsers.add_parser("send", help="Send an email")
    p_send.add_argument("--to", required=True, help="Recipient email")
    p_send.add_argument("--subject", required=True, help="Email subject")
    p_send.add_argument("--body", required=True, help="Email body")
    p_send.add_argument("--cc", help="CC recipients (comma-separated)")
    p_send.add_argument("--bcc", help="BCC recipients (comma-separated)")
    p_send.add_argument("--reply-to", help="Reply-to address")
    p_send.add_argument("--attachment", action="append", help="File path to attach (can be used multiple times)")
    p_send.set_defaults(func=cmd_send)

    # reply - reply to an email
    p_reply = subparsers.add_parser("reply", help="Reply to an email")
    p_reply.add_argument("id", help="Original email ID to reply to")
    p_reply.add_argument("--body", required=True, help="Reply body")
    p_reply.add_argument("--to", help="Override recipient address")
    p_reply.add_argument("--cc", help="CC recipients (comma-separated)")
    p_reply.add_argument("--bcc", help="BCC recipients (comma-separated)")
    p_reply.set_defaults(func=cmd_reply)

    # drafts - manage drafts
    p_drafts = subparsers.add_parser("drafts", help="Manage email drafts")
    drafts_sub = p_drafts.add_subparsers(dest="drafts_cmd")

    # drafts list
    p_drafts_list = drafts_sub.add_parser("list", help="List all drafts")
    p_drafts_list.add_argument("-n", "--limit", type=int, default=20, help="Max drafts to fetch")
    p_drafts_list.set_defaults(func=cmd_drafts_list)

    # drafts create
    p_drafts_create = drafts_sub.add_parser("create", help="Create a new draft")
    p_drafts_create.add_argument("--to", required=True, help="Recipient email")
    p_drafts_create.add_argument("--subject", required=True, help="Email subject")
    p_drafts_create.add_argument("--body", required=True, help="Email body")
    p_drafts_create.add_argument("--cc", help="CC recipients")
    p_drafts_create.add_argument("--bcc", help="BCC recipients")
    p_drafts_create.add_argument("--attachment", action="append", help="File to attach")
    p_drafts_create.set_defaults(func=cmd_draft)

    # drafts reply - create a draft reply
    p_drafts_reply = drafts_sub.add_parser("reply", help="Create a draft reply to an email")
    p_drafts_reply.add_argument("id", help="Original email ID to reply to")
    p_drafts_reply.add_argument("--body", required=True, help="Reply body")
    p_drafts_reply.add_argument("--cc", help="CC recipients (comma-separated)")
    p_drafts_reply.set_defaults(func=cmd_draft_reply)

    # drafts delete
    p_drafts_delete = drafts_sub.add_parser("delete", help="Delete a draft")
    p_drafts_delete.add_argument("id", help="Draft ID to delete")
    p_drafts_delete.set_defaults(func=cmd_draft_delete)

    # drafts send
    p_drafts_send = drafts_sub.add_parser("send", help="Send an existing draft")
    p_drafts_send.add_argument("id", help="Draft ID to send")
    p_drafts_send.set_defaults(func=cmd_draft_send)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "labels" and not args.labels_cmd:
        p_labels.print_help()
        sys.exit(1)

    if args.command == "filters" and not args.filters_cmd:
        p_filters.print_help()
        sys.exit(1)

    if args.command == "drafts" and not args.drafts_cmd:
        p_drafts.print_help()
        sys.exit(1)

    # For accounts command, no account needed
    if args.command == "accounts":
        args.func(args)
        return

    try:
        args.func(args)
    except AuthExpiredError as e:
        print(json.dumps({
            "status": "auth_expired",
            "account": e.account_name,
            "message": f"Auth expired for account '{e.account_name}'. User must re-authenticate manually.",
            "command": e.reauth_command,
        }))


if __name__ == "__main__":
    main()

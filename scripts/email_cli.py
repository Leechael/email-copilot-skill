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
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime, timedelta

# Add skill directory to sys.path for local imports
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SKILL_DIR)
from gmail_client import GmailClient, get_available_accounts, CONFIG_PATH


def get_client(account: str = None) -> GmailClient:
    """Get authenticated Gmail client for specified account."""
    client = GmailClient(account=account)
    client.authenticate()
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

def cmd_list(args):
    """List emails with optional search query."""
    client = get_client(args.account)

    query = args.query if args.query else "label:INBOX"
    all_msgs = []
    page_token = None

    while len(all_msgs) < args.limit:
        try:
            res = (
                client.service.users()
                .messages()
                .list(
                    userId="me",
                    q=query,
                    maxResults=min(args.limit - len(all_msgs), 500),
                    pageToken=page_token,
                )
                .execute()
            )

            msgs = res.get("messages", [])
            if not msgs:
                if not page_token:
                    break
            else:
                all_msgs.extend(msgs)

            page_token = res.get("nextPageToken")
            if not page_token:
                break
        except Exception as e:
            print(f"<error account='{client.account_email}'>{str(e)}</error>")
            return

    if not all_msgs:
        print(f"<emails account='{client.account_email}' account_name='{client.account_name}' count='0'></emails>")
        return

    # Fetch details in batch
    chunk_size = 50
    output = [f"<emails account='{client.account_email}' account_name='{client.account_name}' count='{len(all_msgs)}'>"]

    for i in range(0, len(all_msgs), chunk_size):
        chunk = all_msgs[i : i + chunk_size]
        batch = client.service.new_batch_http_request()
        batch_resp = {}

        def cb(rid, resp, exc):
            if not exc:
                batch_resp[rid] = resp

        for msg in chunk:
            batch.add(
                client.service.users()
                .messages()
                .get(userId="me", id=msg["id"], format="full"),
                request_id=msg["id"],
                callback=cb,
            )

        try:
            batch.execute()
        except Exception as e:
            output.append(f"  <batch_error>{str(e)}</batch_error>")
            continue

        for mid, data in batch_resp.items():
            payload = data.get("payload", {})
            headers = payload.get("headers", [])

            subject = next(
                (h["value"] for h in headers if h["name"].lower() == "subject"),
                "No Subject",
            )
            sender = next(
                (h["value"] for h in headers if h["name"].lower() == "from"), "Unknown"
            )
            date = next(
                (h["value"] for h in headers if h["name"].lower() == "date"), ""
            )
            snippet = (
                data.get("snippet", "").replace("<", "&lt;").replace(">", "&gt;")
            )
            thread_id = data.get("threadId", "")

            output.append(f"""  <email id="{mid}" thread_id="{thread_id}">
    <from>{sender}</from>
    <subject>{subject}</subject>
    <date>{date}</date>
    <snippet>{snippet}</snippet>
  </email>""")

    output.append("</emails>")
    print("\n".join(output))


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

        payload = msg.get("payload", {})
        headers = payload.get("headers", [])
        subject = get_header(headers, "subject", "No Subject")
        sender = get_header(headers, "from", "Unknown")
        reply_to = get_header(headers, "reply-to") or None

        body = ""
        if "parts" in payload:
            for part in payload["parts"]:
                if part["mimeType"] == "text/plain":
                    data = part["body"].get("data", "")
                    if data:
                        body = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                        break
        elif "body" in payload:
            data = payload["body"].get("data", "")
            if data:
                body = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

        if not body:
            body = msg.get("snippet", "")

        print(f"Account: {client.account_email} ({client.account_name})")
        print(f"Labels: {', '.join(msg.get('labelIds', []))}")
        print(f"Subject: {subject}")
        print(f"From: {sender}")
        if reply_to:
            print(f"Reply-To: {reply_to}")
        print(f"{'-' * 40}\n{body}")

    except Exception as e:
        output_error(str(e), client.account_email)


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


def cmd_labels_list(args):
    """List all Gmail labels."""
    client = get_client(args.account)

    try:
        labels = _list_labels(client)

        output = []
        for label in labels:
            label_info = {
                "id": label.get("id"),
                "name": label.get("name"),
                "type": label.get("type"),
            }
            # Include message counts if available
            if "messagesTotal" in label:
                label_info["messages_total"] = label.get("messagesTotal")
                label_info["messages_unread"] = label.get("messagesUnread")
            output.append(label_info)

        # Sort: system labels first, then user labels alphabetically
        system_labels = [l for l in output if l["type"] == "system"]
        user_labels = sorted([l for l in output if l["type"] == "user"], key=lambda x: x["name"].lower())

        print(json.dumps({
            "status": "success",
            "labels": system_labels + user_labels,
            "count": len(output),
            "user_labels": len(user_labels),
            "account": client.account_email
        }, indent=2))

    except Exception as e:
        output_error(str(e), client.account_email)


def cmd_labels_create(args):
    """Create a new Gmail label."""
    client = get_client(args.account)

    try:
        label_object = {
            "name": args.name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        }
        result = client.service.users().labels().create(
            userId="me", body=label_object
        ).execute()

        output_success({
            "label_id": result.get("id"),
            "name": result.get("name")
        }, client.account_email)

    except Exception as e:
        output_error(str(e), client.account_email)


def cmd_labels_delete(args):
    """Delete a Gmail label by name or ID."""
    client = get_client(args.account)

    try:
        label_id, label_name, label_type = resolve_label(client, args.name_or_id)
        if not label_id:
            output_error(f"Label not found: {args.name_or_id}", client.account_email)
            return

        # Prevent deleting system labels
        if label_type == "system":
            output_error(f"Cannot delete system label: {label_name}", client.account_email)
            return

        client.service.users().labels().delete(userId="me", id=label_id).execute()

        output_success({
            "deleted_label_id": label_id,
            "deleted_label_name": label_name
        }, client.account_email)

    except Exception as e:
        output_error(str(e), client.account_email)


def cmd_labels_rename(args):
    """Rename a Gmail label."""
    client = get_client(args.account)

    try:
        label_id, old_name, label_type = resolve_label(client, args.old_name)
        if not label_id:
            output_error(f"Label not found: {args.old_name}", client.account_email)
            return

        if label_type == "system":
            output_error(f"Cannot rename system label: {old_name}", client.account_email)
            return

        # Update the label
        result = client.service.users().labels().patch(
            userId="me",
            id=label_id,
            body={"name": args.new_name}
        ).execute()

        output_success({
            "label_id": label_id,
            "old_name": old_name,
            "new_name": result.get("name")
        }, client.account_email)

    except Exception as e:
        output_error(str(e), client.account_email)


# =============================================================================
# Filter Management
# =============================================================================

def cmd_filters_list(args):
    """List all Gmail filters."""
    client = get_client(args.account)

    try:
        results = client.service.users().settings().filters().list(userId="me").execute()
        filters = results.get("filter", [])

        if not filters:
            print(json.dumps({"filters": [], "count": 0, "account": client.account_email}))
            return

        output = []
        for f in filters:
            criteria = f.get("criteria", {})
            action = f.get("action", {})

            filter_info = {
                "id": f.get("id"),
                "criteria": {
                    "from": criteria.get("from"),
                    "to": criteria.get("to"),
                    "subject": criteria.get("subject"),
                    "query": criteria.get("query"),
                    "hasAttachment": criteria.get("hasAttachment"),
                },
                "action": {
                    "addLabelIds": action.get("addLabelIds", []),
                    "removeLabelIds": action.get("removeLabelIds", []),
                    "forward": action.get("forward"),
                },
            }
            # Remove None values
            filter_info["criteria"] = {k: v for k, v in filter_info["criteria"].items() if v is not None}
            filter_info["action"] = {k: v for k, v in filter_info["action"].items() if v}
            output.append(filter_info)

        print(json.dumps({"filters": output, "count": len(output), "account": client.account_email}, indent=2))
    except Exception as e:
        output_error(str(e), client.account_email)


def cmd_filters_add(args):
    """Add a new Gmail filter."""
    client = get_client(args.account)

    # Build criteria
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

    # Build action
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

    filter_body = {"criteria": criteria, "action": action}

    try:
        result = client.service.users().settings().filters().create(
            userId="me", body=filter_body
        ).execute()
        output_success({"filter_id": result.get("id")}, client.account_email)
    except Exception as e:
        output_error(str(e), client.account_email)


def cmd_filters_delete(args):
    """Delete a Gmail filter by ID."""
    client = get_client(args.account)

    try:
        client.service.users().settings().filters().delete(
            userId="me", id=args.id
        ).execute()
        output_success({"deleted_id": args.id}, client.account_email)
    except Exception as e:
        output_error(str(e), client.account_email)


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

    # list
    p_list = subparsers.add_parser("list", help="List emails")
    p_list.add_argument("-n", "--limit", type=int, default=100, help="Max emails to fetch")
    p_list.add_argument("-q", "--query", help="Gmail search query")
    p_list.set_defaults(func=cmd_list)

    # read
    p_read = subparsers.add_parser("read", help="Read full email content")
    p_read.add_argument("id", help="Email ID")
    p_read.set_defaults(func=cmd_read)

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
    p_reply.add_argument("--cc", help="CC recipients (comma-separated)")
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

    args.func(args)


if __name__ == "__main__":
    main()

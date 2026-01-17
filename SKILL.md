---
name: email-copilot
description: An intelligent assistant that manages your inbox by learning your preferences, cleaning noise, and prioritizing important work. Use when the user asks about emails, inbox management, or wants to clean up their mailbox.
---

## Goal

Act as a proactive executive assistant for the user's inbox. Filter noise based on dynamic rules, surface critical items, and continuously learn from user feedback.

## First-Time Setup

Before using this skill, install dependencies and check if setup is complete:

```bash
# Install dependencies (one-time)
cd .claude/skills/email-copilot && uv sync && cd -

# Check setup status
uv run --project .claude/skills/email-copilot uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/gmail_client.py --check
```

If `ready: false`, guide the user through setup by reading the README:

```bash
# Read README for detailed setup instructions
cat .claude/skills/email-copilot/README.md
```

**Quick Setup Steps:**

1. **Install deps**: `cd .claude/skills/email-copilot && uv sync`
2. **Google API Credentials**: User needs to create OAuth credentials in Google Cloud Console
3. **Copy credentials**: Save as `.claude/skills/email-copilot/credentials.json`
4. **Create config**: Copy `config.toml.example` to `config.toml`
5. **Authenticate**: Run `uv run --project .claude/skills/email-copilot uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/gmail_client.py --auth default`
6. **Create rules**: Copy `rules.md.example` to `rules.md` and customize

## Multi-Account Support

This skill supports multiple Gmail accounts. Each operation outputs the account email so you know which mailbox you're working with.

**IMPORTANT**: When processing emails from different accounts, apply account-specific rules:
- Work accounts: Prioritize project-related emails, apply stricter cleanup
- Personal accounts: Be more conservative with deletions, preserve receipts

```bash
# List all configured accounts
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py accounts

# Use specific account (add -a before command)
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py -a work list -n 100
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py -a personal list -n 100

# Without -a, uses default account from config.toml
```

## Tools

Unified CLI at `.claude/skills/email-copilot/scripts/email_cli.py`. Run from any directory.

### Account Management

```bash
# List configured accounts
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/gmail_client.py

# Add and authenticate an account
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/gmail_client.py --auth work

# Set default account
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/gmail_client.py --set-default work

# Remove an account
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/gmail_client.py --remove work

# Check setup status (JSON output)
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/gmail_client.py --check
```

### Email Operations

All outputs include `account` field to identify which mailbox the emails belong to.

```bash
# List emails (default: INBOX)
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] list [-n LIMIT] [-q QUERY]

# Examples:
#   list -n 200                          # List 200 emails from default account
#   -a work list -q "is:unread"          # List unread from work account
#   -a personal list -q "from:amazon"    # List Amazon emails from personal

# Read full email content (shows Reply-To header if present)
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] read <msg_id>

# Trash emails
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] trash '<json_id_list>'

# Restore from trash
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] untrash '<json_id_list>'

# Move emails to label (with optional mark-as-read)
# NOTE: Label must exist. Use 'labels list' to verify, or add -c to create if missing.
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] move <label> '<json_id_list>' [-r] [-c]

# Examples:
#   move "Finance/Receipts" '["id1","id2"]' -r     # Move and mark read (label must exist)
#   move "New Label" '["id1"]' -r -c               # Create label if needed
```

### Attachments

```bash
# List attachments in an email
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] attachments <msg_id>

# Download attachments from a specific email
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] download <msg_id> [-o OUTPUT_DIR] [-f FILENAME_FILTER] [-p PREFIX]

# Examples:
#   download abc123 -o ./downloads           # Download to ./downloads
#   download abc123 -f ".pdf"                # Only download PDFs
#   download abc123 -p "invoice"             # Prefix files with "invoice_"

# Search emails and download all attachments
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] search-download -q QUERY [-o OUTPUT_DIR] [-n LIMIT]

# Examples:
#   search-download -q "from:anthropic invoice" -o ./invoices -n 50
#   search-download -q "from:sentry receipt" -o ./receipts
```

### Send Email

```bash
# Send a new email
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] send --to RECIPIENT --subject SUBJECT --body BODY [--cc CC] [--bcc BCC] [--attachment FILE]

# Examples:
#   send --to "user@example.com" --subject "Hello" --body "Hi there!"
#   send --to "user@example.com" --subject "Report" --body "See attached" --attachment ./report.pdf
#   send --to "user@example.com" --subject "Files" --body "Multiple files" --attachment ./a.pdf --attachment ./b.pdf

# Reply to an email (uses Reply-To header if present, otherwise From)
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] reply <msg_id> --body BODY [--cc CC]

# Examples:
#   reply abc123 --body "Thanks for your message!"
#   reply abc123 --body "See below" --cc "team@example.com"
```

### Drafts

```bash
# List all drafts
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] drafts list [-n LIMIT]

# Create a new draft
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] drafts create --to RECIPIENT --subject SUBJECT --body BODY [--cc CC] [--bcc BCC] [--attachment FILE]

# Create a draft reply to an existing email (uses Reply-To header if present)
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] drafts reply <msg_id> --body BODY [--cc CC]

# Delete a draft
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] drafts delete <draft_id>

# Send an existing draft
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] drafts send <draft_id>
```

**Workflow**: When user asks to "save draft" or review before sending:
1. Use `drafts reply` or `drafts create` to save to Gmail
2. User can review/edit in Gmail web interface
3. Use `drafts send` when ready, or user sends manually

### Gmail Labels

Manage Gmail labels (create, list, rename, delete). Use label names, not internal IDs.

```bash
# List all labels (shows ID, name, type; may include message counts when available)
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] labels list

# Create a new label
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] labels create "My Label"

# Delete a label (by name or ID)
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] labels delete "My Label"

# Rename a label (by name; case-insensitive match)
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] labels rename "Old Name" "New Name"
```

**Important:** Always use human-readable label names (e.g., "Cal.com Form"), not internal IDs (e.g., "Label_6").
**Note:** System labels cannot be deleted or renamed.

### Gmail Filters

Filters are account-specific. Always specify account when managing filters.

```bash
# List all filters
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] filters list

# Add a new filter
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] filters add [criteria] [actions]

# Criteria: --from, --to, --subject, --query, --has-attachment
# Actions: --add-label, --archive, --mark-read, --trash, --star, --forward

# Delete a filter
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] filters delete <filter_id>
```

### Maintenance

```bash
# Get email content for summarization
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] summary <label> [-n LIMIT]

# Delete old emails from a label
uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] cleanup <label> [-d DAYS]
```

**Periodic cleanup** (run for each account at end of session):
- `to-be-deleted`: Clean emails older than 30 days
- `Finance/Receipts`: Optional summarization if > 50 emails

### Rules Management

- **Read rules**: Use `Read` tool on `.claude/skills/email-copilot/rules.md`
- **Update rules**: Use `Edit` tool on `.claude/skills/email-copilot/rules.md`

**Gmail Filters vs rules.md**:
- **Gmail Filters**: Server-side automatic rules (archive, mark-read, trash). Use for recurring patterns that should always be handled the same way.
- **rules.md**: Manual rules that the assistant applies during inbox processing. Use for context-dependent decisions that filters can't handle.

Do NOT duplicate filter rules in rules.md. They work together, not redundantly.

## Common Tasks

### Collect Invoices/Receipts

Search and download invoices from various services:

```bash
# Common invoice search queries by service:

# Anthropic/Claude
search-download -q "(from:anthropic) (invoice OR receipt)" -o ./invoices/claude -n 100

# OpenAI/ChatGPT
search-download -q "(from:openai OR from:chatgpt) (invoice OR receipt)" -o ./invoices/openai -n 100

# Sentry
search-download -q "(from:sentry OR from:sentry.io) (invoice OR receipt OR payment)" -o ./invoices/sentry -n 100

# Resend
search-download -q "(from:resend) (invoice OR receipt)" -o ./invoices/resend -n 100

# AWS
search-download -q "(from:aws OR from:amazon) billing" -o ./invoices/aws -n 100

# Google Cloud
search-download -q "(from:google) (invoice OR billing) cloud" -o ./invoices/gcp -n 100

# GitHub
search-download -q "(from:github) (invoice OR receipt OR billing)" -o ./invoices/github -n 100

# Stripe
search-download -q "(from:stripe) (invoice OR receipt)" -o ./invoices/stripe -n 100
```

### Yearly Invoice Summary

To compile invoices for a specific year (e.g., 2025):

1. Search each service across all accounts
2. Download attachments to organized folders
3. Filter by year in the search query when possible: `after:2025/01/01 before:2026/01/01`

Example:
```bash
# Search 2025 invoices
search-download -q "from:anthropic (invoice OR receipt) after:2025/01/01 before:2026/01/01" -o ./2025-invoices/claude
```

## Procedure

1. **Check Accounts**: Run `accounts` to see available accounts and their emails.

2. **Load Context (MANDATORY)**: **MUST** read `rules.md` before processing ANY emails. Use the Read tool on `.claude/skills/email-copilot/rules.md` to load user preferences, auto-trash list, and classification rules. Skipping this step will result in incorrect email handling.

3. **Process Each Account**: For each configured account:
   - Run `list` with `-a <account>` to fetch emails
   - Note the `account` field in output to track which mailbox
   - Apply account-appropriate rules (work vs personal)

4. **Classify & Execute**:
   - Match against **Auto-Trash List** → `trash`
   - **Time check**: Expired notifications → `trash`
   - **Finance**: Receipts/Statements → `move` to appropriate label
   - Use `-a <account>` to ensure operations target correct mailbox

5. **Report & Evolve**:
   - Group remaining emails by account AND category
   - Propose rule updates for ambiguous emails
   - Update `rules.md` if user agrees

## Learning & Evolution

When an email doesn't match existing rules:
1. **Cold sales email** → Propose adding sender to Auto-Trash List
2. **New project/topic** → Propose adding a new Project Keyword
3. **Recurring newsletter** → Propose adding to appropriate Newsletter category

Always ask user before updating `rules.md`.

## Tips

- **Account Context**: Always check `account` field in output before operations
- **Cross-Account**: Don't mix email IDs between accounts - IDs are account-specific
- **Labels**: Always use human-readable label names (e.g., "Cal.com Form"), never internal IDs (e.g., "Label_6"). Use `labels list` to verify label names before moving emails.
- **Reply-To**: The `reply` command automatically uses `Reply-To` header when present (e.g., mailing lists), falling back to `From` header
- **GitHub Bot vs Human**: Check snippet for "approved", "lgtm", or bot names
- **Safety**: If unsure about "Run failed" emails, list in report instead of trashing
- **Attachments**: Use `search-download` for bulk operations, `download` for single emails
- **Send with Care**: Double-check recipient and content before sending

## File Structure

All files are self-contained within the skill directory:

```
.claude/skills/email-copilot/
├── SKILL.md              # This file - skill instructions
├── README.md             # Detailed setup guide
├── pyproject.toml        # Python dependencies (for uv)
├── gmail_client.py       # Account manager & Gmail client
├── config.toml           # Multi-account configuration (user-specific)
├── config.toml.example   # Configuration template
├── credentials.json      # Google OAuth credentials (user-specific)
├── rules.md              # User-defined filtering rules (user-specific)
├── rules.md.example      # Rules template
├── tokens/               # OAuth tokens per account (user-specific)
│   └── *.json
└── scripts/
    └── email_cli.py      # Unified CLI tool
```

**User-specific files** (should be in .gitignore):
- `config.toml`
- `credentials.json`
- `tokens/`
- `rules.md`

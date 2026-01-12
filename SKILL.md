---
name: email-copilot
description: An intelligent assistant that manages your inbox by learning your preferences, cleaning noise, and prioritizing important work. Use when the user asks about emails, inbox management, or wants to clean up their mailbox.
---

## Goal

Act as a proactive executive assistant for the user's inbox. Filter noise based on dynamic rules, surface critical items, and continuously learn from user feedback.

## First-Time Setup

Before using this skill, check if setup is complete by running:

```bash
python .claude/skills/email-copilot/gmail_client.py --check
```

If `ready: false`, guide the user through setup by reading the README:

```bash
# Read README for detailed setup instructions
cat .claude/skills/email-copilot/README.md
```

**Quick Setup Steps:**

1. **Google API Credentials**: User needs to create OAuth credentials in Google Cloud Console
2. **Copy credentials**: Save as `.claude/skills/email-copilot/credentials.json`
3. **Create config**: Copy `config.toml.example` to `config.toml`
4. **Authenticate**: Run `python .claude/skills/email-copilot/gmail_client.py --auth default`
5. **Create rules**: Copy `rules.md.example` to `rules.md` and customize

## Multi-Account Support

This skill supports multiple Gmail accounts. Each operation outputs the account email so you know which mailbox you're working with.

**IMPORTANT**: When processing emails from different accounts, apply account-specific rules:
- Work accounts: Prioritize project-related emails, apply stricter cleanup
- Personal accounts: Be more conservative with deletions, preserve receipts

```bash
# List all configured accounts
python .claude/skills/email-copilot/scripts/email_cli.py accounts

# Use specific account (add -a before command)
python .claude/skills/email-copilot/scripts/email_cli.py -a work list -n 100
python .claude/skills/email-copilot/scripts/email_cli.py -a personal list -n 100

# Without -a, uses default account from config.toml
```

## Tools

Unified CLI at `.claude/skills/email-copilot/scripts/email_cli.py`. Run from any directory.

### Account Management

```bash
# List configured accounts
python .claude/skills/email-copilot/gmail_client.py

# Add and authenticate an account
python .claude/skills/email-copilot/gmail_client.py --auth work

# Set default account
python .claude/skills/email-copilot/gmail_client.py --set-default work

# Remove an account
python .claude/skills/email-copilot/gmail_client.py --remove work

# Check setup status (JSON output)
python .claude/skills/email-copilot/gmail_client.py --check
```

### Email Operations

All outputs include `account` field to identify which mailbox the emails belong to.

```bash
# List emails (default: INBOX)
python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] list [-n LIMIT] [-q QUERY]

# Examples:
#   list -n 200                          # List 200 emails from default account
#   -a work list -q "is:unread"          # List unread from work account
#   -a personal list -q "from:amazon"    # List Amazon emails from personal

# Read full email content
python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] read <msg_id>

# Trash emails
python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] trash '<json_id_list>'

# Restore from trash
python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] untrash '<json_id_list>'

# Move emails to label (with optional mark-as-read)
python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] move <label> '<json_id_list>' [-r]
```

### Attachments

```bash
# List attachments in an email
python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] attachments <msg_id>

# Download attachments from a specific email
python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] download <msg_id> [-o OUTPUT_DIR] [-f FILENAME_FILTER] [-p PREFIX]

# Examples:
#   download abc123 -o ./downloads           # Download to ./downloads
#   download abc123 -f ".pdf"                # Only download PDFs
#   download abc123 -p "invoice"             # Prefix files with "invoice_"

# Search emails and download all attachments
python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] search-download -q QUERY [-o OUTPUT_DIR] [-n LIMIT]

# Examples:
#   search-download -q "from:anthropic invoice" -o ./invoices -n 50
#   search-download -q "from:sentry receipt" -o ./receipts
```

### Send Email

```bash
# Send a new email
python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] send --to RECIPIENT --subject SUBJECT --body BODY [--cc CC] [--bcc BCC] [--attachment FILE]

# Examples:
#   send --to "user@example.com" --subject "Hello" --body "Hi there!"
#   send --to "user@example.com" --subject "Report" --body "See attached" --attachment ./report.pdf
#   send --to "user@example.com" --subject "Files" --body "Multiple files" --attachment ./a.pdf --attachment ./b.pdf

# Reply to an email
python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] reply <msg_id> --body BODY

# Example:
#   reply abc123 --body "Thanks for your message!"
```

### Drafts

```bash
# List all drafts
python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] drafts list [-n LIMIT]

# Create a new draft
python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] drafts create --to RECIPIENT --subject SUBJECT --body BODY [--cc CC] [--bcc BCC] [--attachment FILE]

# Create a draft reply to an existing email
python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] drafts reply <msg_id> --body BODY

# Delete a draft
python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] drafts delete <draft_id>

# Send an existing draft
python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] drafts send <draft_id>
```

**Workflow**: When user asks to "save draft" or review before sending:
1. Use `drafts reply` or `drafts create` to save to Gmail
2. User can review/edit in Gmail web interface
3. Use `drafts send` when ready, or user sends manually

### Gmail Filters

Filters are account-specific. Always specify account when managing filters.

```bash
# List all filters
python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] filters list

# Add a new filter
python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] filters add [criteria] [actions]

# Criteria: --from, --to, --subject, --query, --has-attachment
# Actions: --add-label, --archive, --mark-read, --trash, --star, --forward

# Delete a filter
python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] filters delete <filter_id>
```

### Maintenance

```bash
# Get email content for summarization
python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] summary <label> [-n LIMIT]

# Delete old emails from a label
python .claude/skills/email-copilot/scripts/email_cli.py [-a ACCOUNT] cleanup <label> [-d DAYS]
```

**Periodic cleanup** (run for each account at end of session):
- `to-be-deleted`: Clean emails older than 30 days
- `Finance/Receipts`: Optional summarization if > 50 emails

### Rules Management

- **Read rules**: Use `Read` tool on `.claude/skills/email-copilot/rules.md`
- **Update rules**: Use `Edit` tool on `.claude/skills/email-copilot/rules.md`

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

2. **Load Context**: Read `rules.md` to understand user preferences.

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

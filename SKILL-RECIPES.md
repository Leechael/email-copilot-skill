# Email Copilot - Recipes

Common tasks and search patterns for email management.

## Invoice Collection

Use `search-download` to collect invoices from various services:

```bash
EMAIL="uv run --project .claude/skills/email-copilot python .claude/skills/email-copilot/scripts/email_cli.py"

# AI/ML Services
$EMAIL search-download -q "from:anthropic (invoice OR receipt)" -o ./invoices/claude -n 100
$EMAIL search-download -q "(from:openai OR from:chatgpt) (invoice OR receipt)" -o ./invoices/openai -n 100

# Developer Tools
$EMAIL search-download -q "from:github (invoice OR receipt OR billing)" -o ./invoices/github -n 100
$EMAIL search-download -q "(from:sentry OR from:sentry.io) (invoice OR receipt)" -o ./invoices/sentry -n 100
$EMAIL search-download -q "from:resend (invoice OR receipt)" -o ./invoices/resend -n 100

# Cloud Providers
$EMAIL search-download -q "(from:aws OR from:amazon) billing" -o ./invoices/aws -n 100
$EMAIL search-download -q "from:google (invoice OR billing) cloud" -o ./invoices/gcp -n 100
$EMAIL search-download -q "from:stripe (invoice OR receipt)" -o ./invoices/stripe -n 100
```

## Yearly Invoice Summary

Collect invoices for a specific year (e.g., 2025):

```bash
# Add date range to queries
$EMAIL search-download -q "from:anthropic (invoice OR receipt) after:2025/01/01 before:2026/01/01" -o ./2025-invoices/claude
$EMAIL search-download -q "from:github billing after:2025/01/01 before:2026/01/01" -o ./2025-invoices/github
```

## Bulk Cleanup Patterns

```bash
# Clean old notifications
$EMAIL cleanup "Notifications" -d 30

# Clean old newsletters
$EMAIL cleanup "Newsletters" -d 60

# Clean processed receipts (be careful!)
$EMAIL cleanup "Finance/Receipts" -d 365
```

## Common Filter Recipes

```bash
# Auto-archive GitHub notifications
$EMAIL filters add --from "notifications@github.com" --add-label "GitHub" --archive

# Auto-label receipts
$EMAIL filters add --query "invoice OR receipt OR payment" --add-label "Finance/Receipts"

# Auto-trash marketing
$EMAIL filters add --from "marketing@example.com" --trash
```

## Search Query Tips

Gmail search operators:
- `from:sender@example.com` - From specific sender
- `to:recipient@example.com` - To specific recipient
- `subject:keyword` - Subject contains keyword
- `has:attachment` - Has attachments
- `filename:pdf` - Attachment filename
- `after:2025/01/01` - After date
- `before:2025/12/31` - Before date
- `is:unread` - Unread emails
- `label:name` - Has label (quote if spaces: `label:"My Label"`)
- `OR` / `AND` - Combine conditions
- `-keyword` - Exclude keyword

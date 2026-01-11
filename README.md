# Email Copilot

A Claude Code skill for intelligent Gmail inbox management with multi-account support.

## Features

- **Multi-Account Support**: Manage multiple Gmail accounts from a single interface
- **Intelligent Classification**: Auto-trash spam, categorize emails, prioritize important items
- **Rule-Based Learning**: Evolving rules based on user feedback
- **Attachment Management**: Download and organize attachments in bulk
- **Send & Reply**: Compose and reply to emails directly
- **Gmail Filters**: Create and manage Gmail filters programmatically

## Setup

### 1. Install Python Dependencies

This skill requires Python 3.11+ and the following packages:

```bash
pip install google-auth google-auth-oauthlib google-api-python-client python-dotenv tomlkit
```

Or if using pdm/poetry, add to your project:

```bash
pdm add google-auth google-auth-oauthlib google-api-python-client python-dotenv tomlkit
```

### 2. Google API Credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select existing)
3. Enable the **Gmail API**:
   - Go to "APIs & Services" > "Library"
   - Search for "Gmail API" and enable it
4. Configure **OAuth Consent Screen**:
   - Go to "APIs & Services" > "OAuth consent screen"
   - **Enterprise (Google Workspace)**: Choose "Internal"
   - **Personal Gmail**: Choose "External", then:
     - Fill in app name, support email
     - Add your email to "Test users"
     - Add scopes: `gmail.modify`, `gmail.settings.basic`
5. Create **OAuth Client ID**:
   - Go to "APIs & Services" > "Credentials"
   - Click "Create Credentials" > "OAuth client ID"
   - Choose "Desktop app"
   - Download the JSON file
6. Save the downloaded file as `credentials.json` in this skill directory:
   ```
   .claude/skills/email-copilot/credentials.json
   ```

### 3. Configuration

Copy the example config and customize:

```bash
cp .claude/skills/email-copilot/config.toml.example .claude/skills/email-copilot/config.toml
```

Edit `config.toml` to add your accounts (email will be auto-filled after authentication).

### 4. Authenticate

Run the authentication flow for your first account:

```bash
python .claude/skills/email-copilot/gmail_client.py --auth default
```

This will:
1. Open a browser window for Google OAuth
2. After authorization, save the token to `tokens/default.json`
3. Update `config.toml` with your email address

Add more accounts:

```bash
python .claude/skills/email-copilot/gmail_client.py --auth work
python .claude/skills/email-copilot/gmail_client.py --auth personal
```

### 5. Rules Configuration

Copy the example rules and customize:

```bash
cp .claude/skills/email-copilot/rules.md.example .claude/skills/email-copilot/rules.md
```

Edit `rules.md` to define:
- Your persona and current projects
- Auto-trash senders and subjects
- Email categories and actions

## Usage

### Check Setup Status

```bash
python .claude/skills/email-copilot/gmail_client.py --check
```

### Account Management

```bash
# List all accounts
python .claude/skills/email-copilot/gmail_client.py

# Set default account
python .claude/skills/email-copilot/gmail_client.py --set-default work

# Remove an account
python .claude/skills/email-copilot/gmail_client.py --remove old-account
```

### Email Operations

```bash
# List emails
python .claude/skills/email-copilot/scripts/email_cli.py list -n 100

# List from specific account
python .claude/skills/email-copilot/scripts/email_cli.py -a work list -n 100

# Search emails
python .claude/skills/email-copilot/scripts/email_cli.py list -q "from:github.com is:unread"

# Read email
python .claude/skills/email-copilot/scripts/email_cli.py read <msg_id>

# Trash emails
python .claude/skills/email-copilot/scripts/email_cli.py trash '["id1","id2"]'

# Move emails to label
python .claude/skills/email-copilot/scripts/email_cli.py move "Archive" '["id1"]' -r
```

### Send & Reply

```bash
# Send email
python .claude/skills/email-copilot/scripts/email_cli.py send \
    --to "user@example.com" \
    --subject "Hello" \
    --body "Message body"

# Reply to email
python .claude/skills/email-copilot/scripts/email_cli.py reply <msg_id> --body "Reply text"
```

### Attachments

```bash
# List attachments
python .claude/skills/email-copilot/scripts/email_cli.py attachments <msg_id>

# Download attachments
python .claude/skills/email-copilot/scripts/email_cli.py download <msg_id> -o ./downloads

# Bulk download from search
python .claude/skills/email-copilot/scripts/email_cli.py search-download -q "from:anthropic invoice" -o ./invoices
```

### Gmail Filters

```bash
# List filters
python .claude/skills/email-copilot/scripts/email_cli.py filters list

# Add filter
python .claude/skills/email-copilot/scripts/email_cli.py filters add \
    --from "newsletter@example.com" \
    --add-label "Newsletters" \
    --archive --mark-read

# Delete filter
python .claude/skills/email-copilot/scripts/email_cli.py filters delete <filter_id>
```

## File Structure

```
.claude/skills/email-copilot/
├── SKILL.md              # Skill instructions for Claude
├── README.md             # This file
├── gmail_client.py       # Gmail client & account manager
├── config.toml           # Your configuration (gitignored)
├── config.toml.example   # Configuration template
├── credentials.json      # Google OAuth credentials (gitignored)
├── rules.md              # Your email rules (gitignored)
├── rules.md.example      # Rules template
├── tokens/               # OAuth tokens (gitignored)
│   └── *.json
└── scripts/
    └── email_cli.py      # CLI tool
```

## Troubleshooting

### "Credentials file not found"

Make sure `credentials.json` is in the skill directory (`.claude/skills/email-copilot/`).

### "Account not found in config.toml"

Run authentication first: `python gmail_client.py --auth <account_name>`

### "Token expired" or authentication errors

Delete the token file and re-authenticate:
```bash
rm .claude/skills/email-copilot/tokens/<account>.json
python .claude/skills/email-copilot/gmail_client.py --auth <account>
```

### "Access blocked" during OAuth

For personal Gmail accounts, make sure:
1. Your email is added as a "Test user" in Google Cloud Console
2. The OAuth consent screen is configured correctly

## License

MIT

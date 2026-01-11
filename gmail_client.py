"""
Gmail Client with multi-account support.

Usage:
    # From skill directory
    cd .claude/skills/email-copilot

    # List all configured accounts
    python gmail_client.py

    # Add and authenticate an account
    python gmail_client.py --auth work

    # Set default account
    python gmail_client.py --set-default work
"""
import os
import sys
import base64
import json
import logging
import argparse
from typing import List, Dict, Optional, Any

# Try importing tomllib (Python 3.11+) or fall back to tomli
try:
    import tomllib
except ImportError:
    import tomli as tomllib

import tomlkit
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build, Resource
from googleapiclient.errors import HttpError

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

# Load environment variables
load_dotenv()

# Skill directory (where this file lives)
SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SKILL_DIR, "config.toml")
DEFAULT_CREDENTIALS_PATH = os.path.join(SKILL_DIR, "credentials.json")
DEFAULT_TOKENS_DIR = os.path.join(SKILL_DIR, "tokens")


class GmailClient:
    def __init__(self, account: str = None):
        """
        Initialize Gmail client for a specific account.

        Args:
            account: Account name from config.toml. If None, uses default_account.
        """
        self.config = self._load_config()
        self.creds: Any = None
        self.service: Any = None
        self.account_email: Optional[str] = None

        # Determine which account to use
        self.account_name = account or self.config["gmail"].get("default_account", "default")

        # Get account config
        accounts = self.config.get("accounts", {})
        if self.account_name not in accounts:
            raise ValueError(f"Account '{self.account_name}' not found in config.toml. "
                           f"Available accounts: {list(accounts.keys())}")

        self.account_config = accounts[self.account_name]
        self.scopes = self.config["gmail"]["scopes"]
        self.credentials_path = os.getenv("GMAIL_CREDENTIALS_PATH", DEFAULT_CREDENTIALS_PATH)

        # Token path - resolve relative to skill dir
        token_path = self.account_config.get("token_path", f"tokens/{self.account_name}.json")
        if not os.path.isabs(token_path):
            self.token_path = os.path.join(SKILL_DIR, token_path)
        else:
            self.token_path = token_path

        self.account_email = self.account_config.get("email")

    def _load_config(self) -> Dict:
        """Load configuration from config.toml"""
        try:
            with open(CONFIG_PATH, "rb") as f:
                return tomllib.load(f)
        except FileNotFoundError:
            logging.warning("config.toml not found, using defaults.")
            return {
                "gmail": {
                    "scopes": ["https://www.googleapis.com/auth/gmail.modify"],
                    "default_account": "default",
                },
                "accounts": {
                    "default": {"token_path": "tokens/default.json"}
                }
            }

    def authenticate(self):
        """Handle OAuth2 authentication"""
        # Ensure token directory exists
        token_dir = os.path.dirname(self.token_path)
        if token_dir:
            os.makedirs(token_dir, exist_ok=True)

        if os.path.exists(self.token_path):
            self.creds = Credentials.from_authorized_user_file(
                self.token_path, self.scopes
            )

        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                try:
                    self.creds.refresh(Request())
                except Exception as e:
                    logging.error(f"Error refreshing token: {e}")
                    self.creds = None

            if not self.creds:
                if not os.path.exists(self.credentials_path):
                    logging.error(
                        f"Credentials file not found at: {self.credentials_path}"
                    )
                    logging.error(
                        "Please download credentials.json from Google Cloud Console."
                    )
                    logging.error(
                        f"See README.md in {SKILL_DIR} for setup instructions."
                    )
                    sys.exit(1)

                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_path, self.scopes
                )
                self.creds = flow.run_local_server(port=0)

            # Save the credentials
            with open(self.token_path, "w") as token:
                token.write(self.creds.to_json())

        try:
            self.service = build(
                "gmail", "v1", credentials=self.creds, cache_discovery=False
            )

            # Fetch and update email address if not set
            if not self.account_email:
                self._update_account_email()

        except HttpError as error:
            logging.error(f"An error occurred building the service: {error}")
            sys.exit(1)

    def _update_account_email(self):
        """Fetch email address and update config.toml"""
        try:
            profile = self.service.users().getProfile(userId="me").execute()
            email = profile.get("emailAddress")
            if email:
                self.account_email = email
                self._save_email_to_config(email)
                logging.info(f"Account email updated: {email}")
        except Exception as e:
            logging.warning(f"Could not fetch email address: {e}")

    def _save_email_to_config(self, email: str):
        """Save email address to config.toml"""
        try:
            with open(CONFIG_PATH, "r") as f:
                doc = tomlkit.load(f)

            if "accounts" not in doc:
                doc["accounts"] = {}
            if self.account_name not in doc["accounts"]:
                doc["accounts"][self.account_name] = {}

            doc["accounts"][self.account_name]["email"] = email

            with open(CONFIG_PATH, "w") as f:
                tomlkit.dump(doc, f)
        except Exception as e:
            logging.warning(f"Could not update config.toml: {e}")

    def list_messages(
        self, query: Optional[str] = None, max_results: Optional[int] = None
    ) -> List[Dict]:
        """List messages matching the query"""
        if not self.service:
            logging.error("Service not initialized. Authenticate first.")
            return []

        final_query = (
            query
            if query is not None
            else self.config["gmail"].get("default_query", "is:unread")
        )
        final_max_results = max_results if max_results is not None else 10

        try:
            results = (
                self.service.users()
                .messages()
                .list(userId="me", q=final_query, maxResults=final_max_results)
                .execute()
            )
            messages = results.get("messages", [])
            return messages
        except HttpError as error:
            logging.error(f"An error occurred: {error}")
            return []

    def get_message_detail(self, msg_id: str) -> Dict:
        """Get full details of a specific message"""
        if not self.service:
            logging.error("Service not initialized.")
            return {}

        try:
            message = (
                self.service.users()
                .messages()
                .get(userId="me", id=msg_id, format="full")
                .execute()
            )

            payload = message.get("payload", {})
            headers = payload.get("headers", [])

            subject = next(
                (h["value"] for h in headers if h["name"].lower() == "subject"),
                "No Subject",
            )
            sender = next(
                (h["value"] for h in headers if h["name"].lower() == "from"),
                "Unknown Sender",
            )
            date = next(
                (h["value"] for h in headers if h["name"].lower() == "date"),
                "Unknown Date",
            )

            body = "No plain text body found."
            if "parts" in payload:
                for part in payload["parts"]:
                    if part["mimeType"] == "text/plain":
                        data = part["body"].get("data")
                        if data:
                            body = base64.urlsafe_b64decode(data).decode()
                            break
            elif "body" in payload:
                data = payload["body"].get("data")
                if data:
                    body = base64.urlsafe_b64decode(data).decode()

            return {
                "id": msg_id,
                "threadId": message.get("threadId"),
                "subject": subject,
                "from": sender,
                "date": date,
                "snippet": message.get("snippet"),
                "body": body,
            }
        except HttpError as error:
            logging.error(f"An error occurred fetching message {msg_id}: {error}")
            return {}


def get_available_accounts() -> Dict[str, Dict]:
    """Get all configured accounts from config.toml"""
    try:
        with open(CONFIG_PATH, "rb") as f:
            config = tomllib.load(f)
        return config.get("accounts", {})
    except Exception:
        return {}


def ensure_account(name: str) -> bool:
    """Ensure account exists in config.toml, add if not present."""
    try:
        with open(CONFIG_PATH, "r") as f:
            doc = tomlkit.load(f)

        if "accounts" not in doc:
            doc["accounts"] = {}

        if name in doc["accounts"]:
            return True  # Already exists

        # Add new account
        doc["accounts"][name] = {
            "token_path": f"tokens/{name}.json"
        }

        with open(CONFIG_PATH, "w") as f:
            tomlkit.dump(doc, f)

        print(f"Account '{name}' added to config.")
        return True
    except Exception as e:
        print(f"Error ensuring account: {e}")
        return False


def set_default_account(name: str) -> bool:
    """Set the default account in config.toml"""
    try:
        with open(CONFIG_PATH, "r") as f:
            doc = tomlkit.load(f)

        accounts = doc.get("accounts", {})
        if name not in accounts:
            print(f"Account '{name}' not found.")
            return False

        doc["gmail"]["default_account"] = name

        with open(CONFIG_PATH, "w") as f:
            tomlkit.dump(doc, f)

        print(f"Default account set to '{name}'.")
        return True
    except Exception as e:
        print(f"Error setting default account: {e}")
        return False


def remove_account(name: str):
    """Remove an account from config.toml."""
    try:
        with open(CONFIG_PATH, "r") as f:
            doc = tomlkit.load(f)

        if "accounts" not in doc or name not in doc["accounts"]:
            print(f"Account '{name}' not found.")
            return

        del doc["accounts"][name]

        with open(CONFIG_PATH, "w") as f:
            tomlkit.dump(doc, f)

        print(f"Account '{name}' removed.")
    except Exception as e:
        print(f"Error: {e}")


def list_accounts():
    """List all configured accounts."""
    accounts = get_available_accounts()
    if not accounts:
        print("No accounts configured.")
        print(f"Add one with: python {os.path.basename(__file__)} --auth <name>")
        return

    try:
        with open(CONFIG_PATH, "rb") as f:
            config = tomllib.load(f)
        default = config.get("gmail", {}).get("default_account", "default")
    except:
        default = "default"

    print("Configured accounts:")
    for name, info in accounts.items():
        email = info.get("email", "(not authenticated)")
        is_default = " [default]" if name == default else ""
        print(f"  {name}: {email}{is_default}")


def check_setup() -> dict:
    """Check if the skill is properly set up. Returns status dict."""
    status = {
        "config_exists": os.path.exists(CONFIG_PATH),
        "credentials_exists": os.path.exists(DEFAULT_CREDENTIALS_PATH),
        "accounts": [],
        "ready": False,
    }

    if status["config_exists"]:
        try:
            with open(CONFIG_PATH, "rb") as f:
                config = tomllib.load(f)
            accounts = config.get("accounts", {})
            for name, info in accounts.items():
                token_path = info.get("token_path", f"tokens/{name}.json")
                if not os.path.isabs(token_path):
                    token_path = os.path.join(SKILL_DIR, token_path)
                status["accounts"].append({
                    "name": name,
                    "email": info.get("email"),
                    "authenticated": os.path.exists(token_path),
                })
        except Exception:
            pass

    # Ready if we have credentials and at least one authenticated account
    status["ready"] = (
        status["credentials_exists"] and
        any(a["authenticated"] for a in status["accounts"])
    )

    return status


def main():
    parser = argparse.ArgumentParser(description="Gmail Account Manager")
    parser.add_argument("--auth", metavar="NAME",
                        help="Add (if needed) and authenticate an account")
    parser.add_argument("--set-default", metavar="NAME", help="Set default account")
    parser.add_argument("--list", "-l", action="store_true", help="List all accounts")
    parser.add_argument("--remove", metavar="NAME", help="Remove an account")
    parser.add_argument("--check", action="store_true", help="Check setup status (JSON output)")

    args = parser.parse_args()

    if args.check:
        import json
        print(json.dumps(check_setup(), indent=2))
        return

    if args.set_default:
        set_default_account(args.set_default)
        return

    if args.remove:
        remove_account(args.remove)
        return

    if args.auth:
        name = args.auth
        # Ensure account exists (add if not)
        if not ensure_account(name):
            return

        print(f"Authenticating '{name}'...")
        try:
            client = GmailClient(account=name)
            client.authenticate()
            print(f"Authenticated: {client.account_email}")
        except Exception as e:
            print(f"Error: {e}")
        return

    if args.list:
        list_accounts()
        return

    # Default: list accounts
    list_accounts()


if __name__ == "__main__":
    main()

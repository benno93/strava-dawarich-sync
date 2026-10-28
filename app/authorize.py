"""One-time Strava authorization. Creates tokens.json in DATA_DIR.

1. Run this script and open the printed link in your browser, click "Authorize".
2. Your browser lands on an error page at http://localhost/... - that is expected.
3. Copy the full address from the address bar and paste it here.
"""
from urllib.parse import parse_qs, urlencode, urlparse

from common import env, exchange_token, tokens_file

SCOPE = "read,activity:read_all"


def parse_code(pasted):
    """Accepts the full redirect URL or just the code. Returns (code, granted_scope)."""
    pasted = pasted.strip()
    if "://" not in pasted:
        return pasted or None, ""
    query = parse_qs(urlparse(pasted).query)
    return query.get("code", [None])[0], query.get("scope", [""])[0]


def main():
    client_id = env("STRAVA_CLIENT_ID")
    env("STRAVA_CLIENT_SECRET")
    url = "https://www.strava.com/oauth/authorize?" + urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": "http://localhost/exchange_token",
            "approval_prompt": "force",
            "scope": SCOPE,
        }
    )
    print("\n1. Open this link in your browser and click 'Authorize':\n")
    print(url)
    print("\n2. Paste the full address from your browser's address bar here:")
    code, granted = parse_code(input("> "))

    if not code:
        raise SystemExit("ERROR: no 'code=' found in the pasted address.")
    if granted and "activity:read_all" not in granted:
        raise SystemExit(
            "ERROR: the checkbox for private activities was unticked. "
            "Run again and leave all checkboxes ticked."
        )

    exchange_token({"grant_type": "authorization_code", "code": code})
    print(f"\nDone. Tokens saved to {tokens_file()}.")


if __name__ == "__main__":
    main()

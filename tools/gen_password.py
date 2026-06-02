"""
Generate an admin password hash for Guardian AI.

Usage:
    python tools/gen_password.py
    python tools/gen_password.py "my new password"

Copy the printed ADMIN_PASSWORD_HASH line into your .env file.
Optionally also copy a fresh SECRET_KEY (only needed once).
"""

import sys
import secrets

try:
    from werkzeug.security import generate_password_hash
except ImportError:
    print("werkzeug is required. Install dependencies first: pip install -r requirements.txt")
    sys.exit(1)


def main():
    if len(sys.argv) > 1:
        password = sys.argv[1]
    else:
        try:
            import getpass
            password = getpass.getpass("New admin password: ")
            confirm = getpass.getpass("Confirm password: ")
            if password != confirm:
                print("Passwords do not match.")
                sys.exit(1)
        except Exception:
            print("Could not read password interactively. Pass it as an argument instead.")
            sys.exit(1)

    if not password:
        print("Empty password is not allowed.")
        sys.exit(1)

    print()
    print("# Paste this into your .env file:")
    print("ADMIN_PASSWORD_HASH=" + generate_password_hash(password))
    print()
    print("# If you don't have a SECRET_KEY yet, you can use this one:")
    print("SECRET_KEY=" + secrets.token_hex(32))


if __name__ == "__main__":
    main()

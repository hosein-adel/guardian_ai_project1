"""
Authentication for Guardian AI — single admin role.

Uses Flask server-side sessions. The admin username and the *hashed* password
are read from config (which reads them from .env). No plaintext password is
ever stored.
"""

import functools
import hmac

from flask import session, redirect, url_for, request, jsonify
from werkzeug.security import check_password_hash

import config


# Endpoints that never require authentication.
PUBLIC_ENDPOINTS = {"login", "logout", "static"}


def verify_credentials(username: str, password: str) -> bool:
    """Return True only if username matches and password verifies against the hash."""
    username = (username or "").strip()

    # Constant-time username compare to avoid leaking which field was wrong.
    user_ok = hmac.compare_digest(username, config.ADMIN_USERNAME or "")

    if not config.ADMIN_PASSWORD_HASH:
        # No password configured → refuse all logins (fail closed).
        return False

    pass_ok = check_password_hash(config.ADMIN_PASSWORD_HASH, password or "")
    return user_ok and pass_ok


def is_logged_in() -> bool:
    return session.get("logged_in") is True


def login_user():
    session.clear()
    session["logged_in"] = True
    session["username"] = config.ADMIN_USERNAME
    session.permanent = True


def logout_user():
    session.clear()


def login_required(view):
    """Decorator: redirect HTML requests to /login, return 401 JSON for APIs."""

    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not is_logged_in():
            return _auth_failed_response()
        return view(*args, **kwargs)

    return wrapped


def _auth_failed_response():
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "Authentication required"}), 401
    return redirect(url_for("login", next=request.path))

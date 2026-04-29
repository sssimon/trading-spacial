"""Vanilla HTML for GET /setup — no JavaScript, server-side validation.

Designed to work in lynx, w3m, or any browser with JS disabled. Frontend
React `SetupPage.tsx` provides a nicer UI but uses the same POST /setup
endpoint, so users without the frontend can still complete setup.

The token is embedded as a hidden field; password match validation is
performed server-side (not in JS).
"""
from __future__ import annotations

import html


def render_setup_page(*, token: str, error: str | None = None) -> str:
    """Render the setup form. `error` is shown above the form when set
    (server-side validation rejected the previous submit).
    """
    error_block = ""
    if error:
        error_block = (
            f'<p style="color:#c0392b;background:#fde7e7;padding:8px 12px;'
            f'border:1px solid #c0392b;border-radius:4px;">'
            f'{html.escape(error)}</p>'
        )

    safe_token = html.escape(token, quote=True)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>trading-spacial — first-time setup</title>
<meta name="robots" content="noindex,nofollow">
<style>
 body {{ font-family: -apple-system, "Segoe UI", sans-serif; max-width: 480px;
        margin: 48px auto; padding: 0 16px; color: #222; }}
 h1 {{ font-size: 22px; margin: 0 0 4px 0; }}
 .sub {{ color: #666; margin: 0 0 24px 0; font-size: 13px; }}
 form {{ display: flex; flex-direction: column; gap: 14px;
         border: 1px solid #ddd; border-radius: 6px; padding: 24px; }}
 label {{ display: flex; flex-direction: column; gap: 4px;
          font-size: 13px; color: #555; }}
 input[type=email], input[type=password] {{
   padding: 8px 10px; border: 1px solid #ccc; border-radius: 4px;
   font-size: 14px; }}
 button {{ padding: 10px; background: #238636; color: white; border: none;
           border-radius: 4px; font-size: 14px; font-weight: 600;
           cursor: pointer; }}
 button:hover {{ background: #2ea043; }}
 .rules {{ font-size: 12px; color: #666; line-height: 1.5; margin: 0; }}
 .rules code {{ background: #f0f0f0; padding: 1px 4px; border-radius: 3px; }}
</style>
</head>
<body>
<h1>First-time setup</h1>
<p class="sub">Create the admin user. This page is only available before
the first user is created.</p>
{error_block}
<form method="post" action="/setup">
  <input type="hidden" name="token" value="{safe_token}">
  <label>Email
    <input type="email" name="email" required autofocus
           autocomplete="username">
  </label>
  <label>Password
    <input type="password" name="password" required minlength="12"
           autocomplete="new-password">
  </label>
  <label>Confirm password
    <input type="password" name="confirm_password" required minlength="12"
           autocomplete="new-password">
  </label>
  <p class="rules">
    Requirements: at least 12 characters, ≤ 72 bytes, must contain a
    letter and a digit.
  </p>
  <button type="submit">Create admin and complete setup</button>
</form>
</body>
</html>
"""


def render_completed_redirect() -> str:
    """Tiny page shown after a successful POST /setup. Auto-refreshes to
    /login after 1s. Works without JS (meta refresh)."""
    return """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<title>Setup complete</title>
<meta http-equiv="refresh" content="1; url=/login">
<style>body{font-family:sans-serif;max-width:420px;margin:96px auto;text-align:center;color:#222}</style>
</head><body>
<h1>Setup complete</h1>
<p>Redirecting to login… If nothing happens, <a href="/login">click here</a>.</p>
</body></html>
"""

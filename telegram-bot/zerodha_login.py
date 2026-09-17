"""
Zerodha Kite Connect login helper — automatic callback capture.

AUTO-CALLBACK MODE (default):
  1. Generates a self-signed SSL certificate for localhost.
  2. Starts a local HTTPS server on port 8000.
  3. Prints the Kite login URL -- open it in a browser and complete login + 2FA.
  4. Zerodha redirects to http://localhost:8000/?request_token=XXX&status=success
  5. The server captures the request_token automatically, exchanges it for an
     access_token, saves it to .env, and shuts down.

  If your Kite app's redirect URL is https://localhost:8000/ instead, pass
  nothing (HTTPS is the default).  If it is http://, pass --http.

MANUAL MODE:
  --token <request_token>
      If you already have a request_token (e.g. from a previous attempt or
      the copy/paste flow), exchange it directly without starting a server.

FLAGS:
  --port PORT       (default 8000)  Port for the local callback server.
  --http            Use plain HTTP instead of HTTPS (no SSL certificate).
  --timeout SECS    (default 600)   Max seconds to wait for the callback.

Run:  venv/bin/python telegram-bot/zerodha_login.py
"""
import os
import re
import ssl
import sys
import time
import argparse
import threading
import urllib.parse
import http.server
import ipaddress
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")

DEFAULT_PORT = 8000
DEFAULT_TIMEOUT = 600  # 10 minutes


# ---------------------------------------------------------------------------
# .env helpers
# ---------------------------------------------------------------------------

def update_env(key, value):
    """Update or insert a KEY=VALUE pair in .env."""
    load_dotenv(ENV_PATH)
    text = open(ENV_PATH).read() if os.path.exists(ENV_PATH) else ""

    if re.search(rf"^{re.escape(key)}=.*$", text, flags=re.MULTILINE):
        text = re.sub(
            rf"^{re.escape(key)}=.*$",
            f"{key}={value}",
            text,
            flags=re.MULTILINE,
        )
    else:
        text += f"\n{key}={value}\n"

    open(ENV_PATH, "w").write(text)
    print(f"  Saved {key} to .env")


# ---------------------------------------------------------------------------
# Self-signed certificate generation
# ---------------------------------------------------------------------------

def generate_self_signed_cert(cert_path, key_path):
    """Generate a self-signed SSL certificate for localhost."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "IN"),
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
    ])

    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=90))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.DNSName("127.0.0.1"),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    with open(key_path, "wb") as f:
        f.write(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

    print(f"  Generated self-signed certificate -> {cert_path}")


# ---------------------------------------------------------------------------
# Callback server
# ---------------------------------------------------------------------------

class _Result:
    """Thread-safe container for the callback result."""

    def __init__(self):
        self.token = None
        self.error = None
        self.event = threading.Event()


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Captures the request_token from Kite's OAuth redirect."""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        request_token = params.get("request_token", [None])[0]
        status = params.get("status", [None])[0]
        error_msg = params.get("error", ["unknown error"])[0]

        if request_token and (status == "success" or params.get("action") == ["login"]):
            self.server.result.token = request_token
            self._respond_success(request_token)
        else:
            self.server.result.error = error_msg
            self._respond_error(error_msg)

        # Signal the main thread that we got a response
        self.server.result.event.set()

    def do_POST(self):
        """Handle POST requests (some setups POST the token)."""
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length:
            body = self.rfile.read(content_length).decode("utf-8")
            params.update(urllib.parse.parse_qs(body))

        request_token = params.get("request_token", [None])[0]
        status = params.get("status", [None])[0]

        if request_token and status == "success":
            self.server.result.token = request_token
            self._respond_success(request_token)
        else:
            self.server.result.error = params.get("error", ["unknown error"])[0]
            self._respond_error(params.get("error", ["unknown error"])[0])

        self.server.result.event.set()

    # -- response helpers ---------------------------------------------------

    def _respond_success(self, token):
        masked = f"{token[:8]}...{token[-8:]}" if len(token) > 16 else f"{token[:4]}..."
        body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kite Login Success</title>
  <style>
    body {{ font-family: -apple-system, Segoe UI, sans-serif; text-align: center;
            padding: 60px 20px; background: #f5f5f5; }}
    .card {{ background: white; border-radius: 12px; padding: 40px; max-width: 480px;
             margin: 0 auto; box-shadow: 0 2px 12px rgba(0,0,0,0.08); }}
    h1 {{ color: #27ae60; margin-bottom: 8px; }}
    code {{ background: #ecf0f1; padding: 4px 8px; border-radius: 4px;
            font-size: 13px; word-break: break-all; }}
    p {{ color: #7f8c8d; margin: 12px 0; }}
    .footer {{ margin-top: 20px; font-size: 12px; color: #bdc3c7; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>&#9989; Login Successful!</h1>
    <p>Your request_token has been captured.</p>
    <p><code>{masked}</code></p>
    <p>The terminal is now exchanging it for an access_token.<br>
       You can close this tab.</p>
    <div class="footer">Crypto-Bot Zerodha Login</div>
  </div>
</body>
</html>"""
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _respond_error(self, msg):
        body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Kite Login Error</title>
  <style>
    body {{ font-family: -apple-system, Segoe UI, sans-serif; text-align: center;
            padding: 60px 20px; background: #f5f5f5; }}
    .card {{ background: white; border-radius: 12px; padding: 40px; max-width: 480px;
             margin: 0 auto; box-shadow: 0 2px 12px rgba(0,0,0,0.08); }}
    h1 {{ color: #e74c3c; margin-bottom: 8px; }}
    p {{ color: #7f8c8d; margin: 12px 0; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>&#10060; Login Failed</h1>
    <p>Error: {msg}</p>
    <p>Please return to the terminal, check the error, and try again.</p>
  </div>
</body>
</html>"""
        data = body.encode("utf-8")
        self.send_response(500)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        """Concise server-side logging."""
        print(f"  [SERVER] {args[0]}")


class _CallbackServer(http.server.ThreadingHTTPServer):
    """Threading HTTPS/HTTP server that carries a _Result object."""

    def __init__(self, addr, handler, result, use_https, cert_path, key_path):
        super().__init__(addr, handler)
        self.result = result
        self.daemon_threads = True
        self._use_https = use_https

        if use_https:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certfile=cert_path, keyfile=key_path)
            self.socket = context.wrap_socket(self.socket, server_side=True)

    def server_close(self):
        super().server_close()


def start_callback_server(port, use_https, result):
    """Start the callback server in a background thread."""

    cert_path = key_path = None
    if use_https:
        cert_path = "/tmp/kite_callback_cert.pem"
        key_path = "/tmp/kite_callback_key.pem"
        generate_self_signed_cert(cert_path, key_path)

    server = _CallbackServer(
        ("localhost", port),
        CallbackHandler,
        result,
        use_https,
        cert_path,
        key_path,
    )

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    return server


# ---------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------

def exchange_request_token(request_token, api_key, api_secret):
    """Exchange a request_token for a Kite access_token."""
    from kiteconnect import KiteConnect

    kite = KiteConnect(api_key=api_key)
    session = kite.generate_session(request_token, api_secret=api_secret)
    access_token = session.get("access_token", "")

    if not access_token:
        raise RuntimeError(
            "Kite did not return an access_token. "
            "Check that your API key/secret are correct and the request_token "
            "has not expired (tokens expire in ~1 minute)."
        )

    return access_token


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Zerodha Kite Connect login — automatic callback capture.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--token",
        help="Manually pass a request_token to exchange (skips the server).",
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT,
        help=f"Local port for callback server (default {DEFAULT_PORT}).",
    )
    parser.add_argument(
        "--http", action="store_true",
        help="Use plain HTTP instead of HTTPS (no SSL certificate).",
    )
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT,
        help=f"Max seconds to wait for callback (default {DEFAULT_TIMEOUT}).",
    )
    args = parser.parse_args()

    load_dotenv(ENV_PATH)

    api_key = os.getenv("KITE_API_KEY", "").strip()
    api_secret = os.getenv("KITE_API_SECRET", "").strip()

    if not api_key:
        api_key = input("Kite API key: ").strip()
        update_env("KITE_API_KEY", api_key)

    if not api_secret:
        api_secret = input("Kite API secret: ").strip()
        update_env("KITE_API_SECRET", api_secret)

    # -- Manual mode: just exchange a provided token -----------------------
    if args.token:
        print()
        print("=" * 70)
        print("MANUAL MODE -- exchanging provided request_token ...")
        access_token = exchange_request_token(
            args.token, api_key, api_secret
        )
        update_env("KITE_ACCESS_TOKEN", access_token)
        print()
        print("SUCCESS! Access token saved to .env")
        print("Now set EXCHANGE_PROVIDER=zerodha in .env and run:")
        print("  venv/bin/python telegram-bot/run.py")
        return

    # -- Auto-callback mode -------------------------------------------------
    use_https = not args.http
    result = _Result()

    print()
    print("=" * 70)
    print("AUTOMATIC CALLBACK MODE")
    print("=" * 70)
    print()

    try:
        server = start_callback_server(args.port, use_https, result)
    except OSError as exc:
        print(f"[ERROR] Could not start server on port {args.port}: {exc}")
        print("  Is something already running on that port? Try --port <n>.")
        print("  Or use --token <request_token> for manual mode.")
        sys.exit(1)

    # Build the login URL
    from kiteconnect import KiteConnect

    kite = KiteConnect(api_key=api_key)
    redirect_scheme = "https" if use_https else "http"
    redirect_url = f"{redirect_scheme}://localhost:{args.port}/"
    login_url = f"{kite.login_url()}&redirect_url={urllib.parse.quote(redirect_url)}"

    print(f"Callback server listening on {redirect_url}")
    if use_https:
        print("  (self-signed cert -- browser will warn, click 'Advanced' -> 'Proceed')")
    print()
    print("-" * 70)
    print("STEP 1 : Open this URL in your browser and LOG IN to Zerodha:")
    print()
    print("  ", login_url)
    print()
    print("-" * 70)
    if use_https:
        print("STEP 2 : After login the browser may show a security warning")
        print("         (self-signed cert). Click 'Advanced' > 'Proceed to")
        print("         localhost (unsafe)'. You only do this once.")
    else:
        print("STEP 2 : After login the browser redirects to the local server.")
    print("STEP 3 : Complete Zerodha's 2FA verification.")
    print("         The script will auto-capture your request_token,")
    print("         exchange it for an access_token, and save it to .env.")
    print()
    print(f"  Waiting for callback (timeout {args.timeout}s)...")
    print("=" * 70)

    # Wait for the callback (or timeout)
    got = result.event.wait(timeout=args.timeout)

    try:
        server.shutdown()
        server.server_close()
    except Exception:
        pass

    if not got:
        print()
        print("[TIMEOUT] No callback received within the timeout period.")
        print("  Options:")
        print(f"    1. Your Kite app's redirect URL must be: {redirect_url}")
        print("       (update at https://kite.trade/connect/login)")
        print("    2. Or run in manual mode:")
        print("       venv/bin/python telegram-bot/zerodha_login.py --token <request_token>")
        sys.exit(1)

    if result.error:
        print()
        print(f"[ERROR] Kite redirect reported an error: {result.error}")
        print("  Check your Zerodha login and try again.")
        sys.exit(1)

    request_token = result.token
    print()
    print(f"[CAPTURED] request_token: {request_token[:8]}...{request_token[-8:]}")
    print("  Exchanging for access_token ...")

    try:
        access_token = exchange_request_token(
            request_token, api_key, api_secret
        )
    except Exception as exc:
        print()
        print(f"[ERROR] Failed to exchange request_token: {exc}")
        print("  The request_token may have expired (they expire in ~1 minute).")
        print("  Run the script again to get a fresh one.")
        sys.exit(1)

    update_env("KITE_ACCESS_TOKEN", access_token)

    print()
    print("=" * 70)
    print("SUCCESS! Access token saved to .env")
    print("Now set EXCHANGE_PROVIDER=zerodha in .env and run:")
    print("  venv/bin/python telegram-bot/run.py")
    print("=" * 70)


if __name__ == "__main__":
    main()

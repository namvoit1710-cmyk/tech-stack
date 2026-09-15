"""E-mail clients for the Email worker: SMTP relay + Microsoft Graph sendMail."""

import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any, Dict, List, Optional

import httpx


class EmailError(Exception):
    """Base exception for e-mail send errors."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        error_code: Optional[str] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class EmailAuthError(EmailError):
    """Raised when the SMTP login or OAuth2 client credentials are rejected."""

    pass


class EmailRecipientsRefusedError(EmailError):
    """Raised when the server refuses one or more recipients."""

    pass


class EmailRateLimitError(EmailError):
    """Raised when the provider rate-limits the send (e.g. Graph 429)."""

    def __init__(
        self,
        message: str,
        retry_after: Optional[int] = None,
        status_code: int = 429,
        error_code: Optional[str] = None,
    ):
        super().__init__(message, status_code, error_code)
        self.retry_after = retry_after


class SmtpEmailClient:
    """Client for sending mail through an SMTP relay.

    Credentials (username/password) come from the vault-resolved credential —
    this client never reads them from anywhere else.
    """

    def __init__(self, timeout: float = 30.0):
        """Initialize the SMTP client.

        Args:
            timeout: Socket timeout for the SMTP session in seconds.
        """
        self.timeout = timeout

    def send(
        self,
        host: str,
        port: int,
        security: str,
        username: str,
        password: str,
        from_address: str,
        to: List[str],
        cc: List[str],
        bcc: List[str],
        subject: str,
        body: str,
        html: bool = False,
    ) -> Dict[str, Any]:
        """Send an e-mail through the SMTP relay.

        Args:
            host: SMTP server hostname.
            port: SMTP server port.
            security: One of "starttls", "ssl", "none".
            username: Login username (empty string skips login).
            password: Login password.
            from_address: Envelope + header From address.
            to: To recipients.
            cc: Cc recipients.
            bcc: Bcc recipients (envelope only, no header).
            subject: Message subject.
            body: Message body.
            html: Send the body as text/html instead of text/plain.

        Returns:
            Dictionary with success status and the generated Message-ID.

        Raises:
            EmailAuthError: If the SMTP login is rejected.
            EmailRecipientsRefusedError: If recipients are refused.
            EmailError: For any other SMTP failure.
        """
        message = EmailMessage()
        message["From"] = from_address
        message["To"] = ", ".join(to)
        if cc:
            message["Cc"] = ", ".join(cc)
        message["Subject"] = subject
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid()

        if html:
            message.set_content("This message requires an HTML-capable mail client.")
            message.add_alternative(body, subtype="html")
        else:
            message.set_content(body)

        recipients = [*to, *cc, *bcc]

        try:
            if security == "ssl":
                smtp = smtplib.SMTP_SSL(
                    host, port, timeout=self.timeout, context=ssl.create_default_context()
                )
            else:
                smtp = smtplib.SMTP(host, port, timeout=self.timeout)

            with smtp:
                if security == "starttls":
                    smtp.starttls(context=ssl.create_default_context())
                if username:
                    smtp.login(username, password or "")
                refused = smtp.send_message(
                    message, from_addr=from_address, to_addrs=recipients
                )

            if refused:
                raise EmailRecipientsRefusedError(
                    f"Recipients refused by the server: {sorted(refused)}",
                    error_code="recipients_refused",
                )

            return {"success": True, "response": message["Message-ID"]}

        except smtplib.SMTPAuthenticationError as e:
            raise EmailAuthError(
                f"SMTP authentication failed: {e}",
                status_code=e.smtp_code,
                error_code="auth_failed",
            ) from e
        except smtplib.SMTPRecipientsRefused as e:
            raise EmailRecipientsRefusedError(
                f"All recipients refused: {sorted(e.recipients)}",
                error_code="recipients_refused",
            ) from e
        except smtplib.SMTPException as e:
            raise EmailError(f"SMTP error: {e}", error_code="smtp_error") from e
        except OSError as e:
            raise EmailError(
                f"SMTP connection failed: {e}", error_code="connection_failed"
            ) from e

    def test_connection(
        self,
        host: str,
        port: int,
        security: str,
        username: str,
        password: str,
    ) -> Dict[str, Any]:
        """Test SMTP connectivity (and login when a username is given).

        Args:
            host: SMTP server hostname.
            port: SMTP server port.
            security: One of "starttls", "ssl", "none".
            username: Login username (empty string skips login).
            password: Login password.

        Returns:
            Dictionary with success status and latency.
        """
        start = time.monotonic()
        try:
            if security == "ssl":
                smtp = smtplib.SMTP_SSL(
                    host, port, timeout=self.timeout, context=ssl.create_default_context()
                )
            else:
                smtp = smtplib.SMTP(host, port, timeout=self.timeout)

            with smtp:
                if security == "starttls":
                    smtp.starttls(context=ssl.create_default_context())
                if username:
                    smtp.login(username, password or "")

            latency_ms = (time.monotonic() - start) * 1000
            return {"success": True, "latency_ms": round(latency_ms, 2), "error": None}

        except (smtplib.SMTPException, OSError) as e:
            latency_ms = (time.monotonic() - start) * 1000
            return {
                "success": False,
                "latency_ms": round(latency_ms, 2),
                "error": str(e),
            }


class GraphEmailClient:
    """Client for Microsoft Graph sendMail (OAuth2 client-credentials flow).

    The token endpoint, client id, and client secret come from the vault-resolved
    credential (generic-oauth2-app); a pre-issued bearer token is also accepted.
    """

    DEFAULT_SCOPE = "https://graph.microsoft.com/.default"

    def __init__(
        self,
        graph_base_url: str = "https://graph.microsoft.com/v1.0",
        timeout: float = 30.0,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        """Initialize the Graph client.

        Args:
            graph_base_url: Microsoft Graph API base URL.
            timeout: Default timeout for HTTP requests in seconds.
            transport: Optional httpx transport (used by tests).
        """
        self.graph_base_url = graph_base_url.rstrip("/")
        self.timeout = timeout
        self._transport = transport

    def fetch_token(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        scopes: str = "",
    ) -> str:
        """Fetch an access token via the OAuth2 client-credentials grant.

        Args:
            token_url: OAuth2 token endpoint.
            client_id: OAuth2 client id.
            client_secret: OAuth2 client secret.
            scopes: Comma- or space-separated scopes; defaults to Graph .default.

        Returns:
            The access token string.

        Raises:
            EmailAuthError: If the token endpoint rejects the client credentials.
            EmailError: For any other token endpoint failure.
        """
        scope = (scopes or "").replace(",", " ").strip() or self.DEFAULT_SCOPE
        data = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope,
        }

        try:
            with httpx.Client(
                timeout=self.timeout, transport=self._transport
            ) as client:
                response = client.post(token_url, data=data)
        except httpx.TimeoutException as e:
            raise EmailError(
                f"Token request timed out: {str(e)}", error_code="timeout"
            ) from e
        except httpx.HTTPError as e:
            raise EmailError(f"HTTP error: {str(e)}", error_code="http_error") from e

        if response.status_code in (400, 401, 403):
            # Do not echo the provider response body — it can carry sensitive detail.
            raise EmailAuthError(
                f"Token endpoint rejected the client credentials: HTTP {response.status_code}",
                status_code=response.status_code,
                error_code="auth_failed",
            )
        if response.status_code >= 400:
            raise EmailError(
                f"Token endpoint returned HTTP {response.status_code}",
                status_code=response.status_code,
                error_code="token_error",
            )

        payload = response.json()
        if "access_token" not in payload:
            raise EmailError(
                "Token endpoint response missing access_token",
                error_code="token_error",
            )
        return payload["access_token"]

    def send_mail(
        self,
        access_token: str,
        sender: str,
        to: List[str],
        cc: List[str],
        bcc: List[str],
        subject: str,
        body: str,
        html: bool = False,
        save_to_sent: bool = True,
    ) -> Dict[str, Any]:
        """Send an e-mail via Microsoft Graph sendMail.

        Args:
            access_token: Bearer token for Graph.
            sender: Mailbox to send as (userPrincipalName or user id).
            to: To recipients.
            cc: Cc recipients.
            bcc: Bcc recipients.
            subject: Message subject.
            body: Message body.
            html: Send the body as HTML instead of plain text.
            save_to_sent: Save a copy to the sender's Sent Items.

        Returns:
            Dictionary with success status.

        Raises:
            EmailAuthError: If Graph rejects the token (401/403).
            EmailRateLimitError: If Graph rate-limits the send (429).
            EmailError: For any other Graph failure.
        """
        message: Dict[str, Any] = {
            "subject": subject,
            "body": {
                "contentType": "HTML" if html else "Text",
                "content": body,
            },
            "toRecipients": self._recipients(to),
        }
        if cc:
            message["ccRecipients"] = self._recipients(cc)
        if bcc:
            message["bccRecipients"] = self._recipients(bcc)

        payload = {"message": message, "saveToSentItems": save_to_sent}
        url = f"{self.graph_base_url}/users/{sender}/sendMail"

        try:
            with httpx.Client(
                timeout=self.timeout, transport=self._transport
            ) as client:
                response = client.post(
                    url,
                    json=payload,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
        except httpx.TimeoutException as e:
            raise EmailError(
                f"Graph request timed out: {str(e)}", error_code="timeout"
            ) from e
        except httpx.HTTPError as e:
            raise EmailError(f"HTTP error: {str(e)}", error_code="http_error") from e

        if response.status_code == 202:
            return {"success": True, "response": "202 Accepted"}

        if response.status_code in (401, 403):
            raise EmailAuthError(
                f"Graph rejected the access token: HTTP {response.status_code}",
                status_code=response.status_code,
                error_code="auth_failed",
            )
        if response.status_code == 404:
            raise EmailError(
                f"Sender mailbox not found: {sender}",
                status_code=404,
                error_code="sender_not_found",
            )
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise EmailRateLimitError(
                "Graph rate limit exceeded. Please try again later.",
                retry_after=int(retry_after) if retry_after else None,
                error_code="rate_limit",
            )

        raise EmailError(
            f"Graph sendMail failed with status {response.status_code}: {response.text}",
            status_code=response.status_code,
            error_code="graph_error",
        )

    @staticmethod
    def _recipients(addresses: List[str]) -> List[Dict[str, Any]]:
        """Map plain addresses to Graph recipient objects."""
        return [{"emailAddress": {"address": address}} for address in addresses]

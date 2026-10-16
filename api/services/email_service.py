"""
SES email sender — used by the digest worker.

Silently noops when DIGEST_FROM_EMAIL is unset (local dev). Logs the
SES message id on success so we can correlate with bounce/complaint
notifications later.
"""
import logging
from typing import Iterable, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from config import AWS_REGION, DIGEST_FROM_EMAIL, DIGEST_REPLY_TO

logger = logging.getLogger(__name__)

_ses_client = None


def _client():
    global _ses_client
    if _ses_client is None:
        _ses_client = boto3.client("ses", region_name=AWS_REGION)
    return _ses_client


def send_email(
    to: str,
    subject: str,
    body_html: str,
    body_text: str,
    reply_to: Optional[str] = None,
    configuration_set: Optional[str] = None,
) -> Optional[str]:
    """Send one email via SES. Returns the SES MessageId on success, None on
    failure or when the sender is unconfigured. Never raises — callers should
    treat None as "did not send" and move on."""
    if not DIGEST_FROM_EMAIL:
        logger.debug("DIGEST_FROM_EMAIL unset; skipping send to %s", to)
        return None

    msg = {
        "Source": DIGEST_FROM_EMAIL,
        "Destination": {"ToAddresses": [to]},
        "Message": {
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {
                "Html": {"Data": body_html, "Charset": "UTF-8"},
                "Text": {"Data": body_text, "Charset": "UTF-8"},
            },
        },
    }
    reply = reply_to or DIGEST_REPLY_TO
    if reply:
        msg["ReplyToAddresses"] = [reply]
    if configuration_set:
        msg["ConfigurationSetName"] = configuration_set

    try:
        resp = _client().send_email(**msg)
        mid = resp.get("MessageId")
        logger.info("SES sent to %s (MessageId=%s)", to, mid)
        return mid
    except (BotoCoreError, ClientError) as exc:
        logger.warning("SES send to %s failed: %s", to, exc)
        return None


def send_emails(messages: Iterable[dict]) -> int:
    """Convenience for sending many; returns count actually sent.
    Each dict: {to, subject, body_html, body_text, reply_to?}."""
    n = 0
    for m in messages:
        if send_email(**m) is not None:
            n += 1
    return n

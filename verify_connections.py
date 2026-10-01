"""Read-only connection checks. Never prints secret values."""

import os
import sys

import requests
from dotenv import load_dotenv
from app.ssi import SSIDataClient


load_dotenv()
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def extract_tier(data) -> str:
    """Accept current and legacy verification response shapes."""
    if isinstance(data, list):
        for item in data:
            tier = extract_tier(item)
            if tier != "UNKNOWN":
                return tier
        return "UNKNOWN"
    if not isinstance(data, dict):
        return "UNKNOWN"
    for key in ("tier", "subscription_tier", "plan", "package"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    subscription = data.get("subscription")
    if isinstance(subscription, str) and subscription.strip():
        return subscription.strip().upper()
    if isinstance(subscription, (dict, list)):
        tier = extract_tier(subscription)
        if tier != "UNKNOWN":
            return tier
    for key in ("data", "result", "license", "membership", "user"):
        nested = data.get(key)
        if isinstance(nested, (dict, list)):
            tier = extract_tier(nested)
            if tier != "UNKNOWN":
                return tier
    return "UNKNOWN"


def main() -> int:
    failed = False
    key = os.getenv("VNSTOCK_API_KEY", "")
    try:
        response = requests.get(
            "https://vnstocks.com/api/vnstock/license/verify",
            params={"api_key": key, "device_id": "telegram-bot-setup"},
            timeout=20,
        )
        data = response.json() if response.ok else {}
        tier = extract_tier(data)
        shape = type(data).__name__
        print(f"VNSTOCK: {'OK' if response.ok else 'FAILED'}; tier={tier}; response={shape}; http={response.status_code}")
        failed |= not response.ok
    except Exception as exc:
        label = "NETWORK ERROR" if isinstance(exc, requests.RequestException) else "RESPONSE ERROR"
        print(f"VNSTOCK: {label} ({type(exc).__name__})")
        failed = True

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    try:
        response = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=20)
        data = response.json()
        result = data.get("result", {}) if response.ok else {}
        print(f"TELEGRAM: {'OK' if response.ok and data.get('ok') else 'FAILED'}; username={result.get('username', 'N/A')}")
        failed |= not (response.ok and data.get("ok"))
    except Exception as exc:
        print(f"TELEGRAM: NETWORK ERROR ({type(exc).__name__})")
        failed = True

    openai_key = os.getenv("OPENAI_API_KEY", "")
    if not openai_key:
        print("OPENAI: SKIPPED")
    else:
        try:
            response = requests.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                json={"model": os.getenv("OPENAI_MODEL", "gpt-5-mini"), "input": "Reply only OK.",
                      "max_output_tokens": 64, "store": False}, timeout=30)
            detail = ""
            if not response.ok:
                try:
                    error = response.json().get("error", {})
                    code = str(error.get("code") or error.get("type") or "unknown")
                    message = " ".join(str(error.get("message") or "").split())[:240]
                    detail = f"; code={code}; message={message}"
                except (ValueError, AttributeError):
                    detail = "; response body unavailable"
            print(f"OPENAI: {'OK' if response.ok else 'FAILED'}; http={response.status_code}{detail}")
            failed |= not response.ok
        except Exception as exc:
            print(f"OPENAI: NETWORK ERROR ({type(exc).__name__})")
            failed = True

    ssi_id = os.getenv("SSI_CONSUMER_ID") or os.getenv("CONSUMERID_SSI") or ""
    ssi_secret = os.getenv("SSI_CONSUMER_SECRET") or os.getenv("CONSUMERSECRET_SSI") or ""
    if not (ssi_id and ssi_secret):
        print("SSI DATA: SKIPPED; missing ConsumerID/ConsumerSecret")
    else:
        try:
            client = SSIDataClient(ssi_id, ssi_secret,
                                   os.getenv("SSI_DATA_BASE_URL", "https://fc-data.ssi.com.vn/api/v2/Market"))
            result = client.healthcheck()
            print(f"SSI DATA: OK; token received; latency_ms={result['latency_ms']}")
        except Exception as exc:
            print(f"SSI DATA: FAILED ({type(exc).__name__})")
            failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())

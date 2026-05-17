#!/usr/bin/env python3
"""
FatSecret account data fetcher.

Скрипт получает данные из FatSecret API для вашего аккаунта.

Поддерживается 2 варианта авторизации:
1) Через готовый токен доступа (FATSECRET_ACCESS_TOKEN)
2) Через получение app-token по client_credentials

Важно: для данных именно вашего аккаунта обычно нужен user access token
(полученный через OAuth авторизацию пользователя в вашем приложении).
"""

import json
import os
import sys
import hmac
import base64
import hashlib
import secrets
import time
from datetime import date, timedelta
from pathlib import Path
from urllib import parse, request
from urllib.error import HTTPError

from dotenv import load_dotenv


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


class FatSecretApp:
    API_URL = "https://platform.fatsecret.com/rest/server.api"
    API_V2_FOOD_ENTRIES_URL = "https://platform.fatsecret.com/rest/food-entries/v2"
    OAUTH_REQUEST_TOKEN_URL = "https://www.fatsecret.com/oauth/request_token"
    OAUTH_AUTHORIZE_URL = "https://www.fatsecret.com/oauth/authorize"
    OAUTH_ACCESS_TOKEN_URL = "https://www.fatsecret.com/oauth/access_token"

    def __init__(self) -> None:
        self.access_token = None

    def load_config(self) -> None:
        env_path = Path(".env")
        if env_path.exists():
            load_dotenv(env_path)
            print("✓ Loaded config from .env")
        else:
            print("⚠ .env not found, using environment variables")

        self.client_id = os.getenv("FATSECRET_CLIENT_ID")
        self.client_secret = os.getenv("FATSECRET_CLIENT_SECRET")
        self.scope = os.getenv("FATSECRET_SCOPE", "basic")
        self.food_entry_id = os.getenv("FATSECRET_FOOD_ENTRY_ID")

        # OAuth 1.0a (FatSecret v1 style)
        # IMPORTANT: do not fallback to OAuth2 credentials here - they can differ.
        self.oauth_consumer_key = os.getenv("FATSECRET_CONSUMER_KEY", "")
        self.oauth_consumer_secret = os.getenv("FATSECRET_CONSUMER_SECRET", "")
        self.oauth_token = os.getenv("FATSECRET_OAUTH_TOKEN", "")
        self.oauth_token_secret = os.getenv("FATSECRET_OAUTH_TOKEN_SECRET", "")
        self.oauth_verifier = os.getenv("FATSECRET_OAUTH_VERIFIER", "")

        # Optional OAuth2 token still supported as fallback, but default flow is OAuth1.
        self.access_token = os.getenv("FATSECRET_ACCESS_TOKEN")

        self.log_date = os.getenv("FATSECRET_LOG_DATE", date.today().isoformat())

    @staticmethod
    def _oauth_quote(value) -> str:
        return parse.quote(str(value), safe="~-._")

    def _build_oauth1_params(self) -> dict:
        params = {
            "oauth_consumer_key": self.oauth_consumer_key,
            "oauth_nonce": secrets.token_hex(16),
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": str(int(time.time())),
            "oauth_version": "1.0",
        }
        if self.oauth_token:
            params["oauth_token"] = self.oauth_token
        return params

    @staticmethod
    def _parse_form_response(text: str) -> dict:
        parsed = parse.parse_qs(text, keep_blank_values=True)
        return {k: (v[0] if isinstance(v, list) and v else "") for k, v in parsed.items()}

    def _sign_oauth1(self, method: str, base_url: str, all_params: dict, token_secret: str | None = None) -> str:
        items = []
        for k, v in all_params.items():
            if isinstance(v, (list, tuple)):
                for vv in v:
                    items.append((self._oauth_quote(k), self._oauth_quote(vv)))
            else:
                items.append((self._oauth_quote(k), self._oauth_quote(v)))
        items.sort()
        param_str = "&".join(f"{k}={v}" for k, v in items)

        base_string = "&".join([
            self._oauth_quote(method.upper()),
            self._oauth_quote(base_url),
            self._oauth_quote(param_str),
        ])

        effective_token_secret = self.oauth_token_secret if token_secret is None else token_secret
        signing_key = f"{self._oauth_quote(self.oauth_consumer_secret)}&{self._oauth_quote(effective_token_secret)}"
        digest = hmac.new(signing_key.encode("utf-8"), base_string.encode("utf-8"), hashlib.sha1).digest()
        return base64.b64encode(digest).decode("ascii")

    def _oauth1_get(self, base_url: str, params: dict):
        oauth_params = self._build_oauth1_params()
        signed_params = {**params, **oauth_params}
        signed_params["oauth_signature"] = self._sign_oauth1("GET", base_url, signed_params)

        url = f"{base_url}?{parse.urlencode(signed_params, quote_via=parse.quote)}"
        print(f"url - {url}")

        req = request.Request(url, method="GET")
        with request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))

    def _oauth1_post_form(self, base_url: str, token: str = "", token_secret: str = "", extra_oauth_params: dict | None = None) -> dict:
        oauth_params = {
            "oauth_consumer_key": self.oauth_consumer_key,
            "oauth_nonce": secrets.token_hex(16),
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": str(int(time.time())),
            "oauth_version": "1.0",
        }
        if token:
            oauth_params["oauth_token"] = token
        if extra_oauth_params:
            oauth_params.update(extra_oauth_params)

        signed_params = dict(oauth_params)
        signature = self._sign_oauth1("POST", base_url, signed_params, token_secret=token_secret)
        oauth_params["oauth_signature"] = signature

        auth_parts = []
        for k in sorted(oauth_params.keys()):
            auth_parts.append(f'{self._oauth_quote(k)}="{self._oauth_quote(oauth_params[k])}"')
        auth_header = "OAuth " + ", ".join(auth_parts)

        req = request.Request(
            base_url,
            headers={
                "Authorization": auth_header,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data=b"",
            method="POST",
        )
        with request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            return self._parse_form_response(body)

    def _oauth1_get_form(self, base_url: str, token: str = "", token_secret: str = "", extra_oauth_params: dict | None = None) -> dict:
        oauth_params = {
            "oauth_consumer_key": self.oauth_consumer_key,
            "oauth_nonce": secrets.token_hex(16),
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": str(int(time.time())),
            "oauth_version": "1.0",
        }
        if token:
            oauth_params["oauth_token"] = token
        if extra_oauth_params:
            oauth_params.update(extra_oauth_params)

        oauth_params["oauth_signature"] = self._sign_oauth1("GET", base_url, oauth_params, token_secret=token_secret)
        url = f"{base_url}?{parse.urlencode(oauth_params, quote_via=parse.quote)}"
        req = request.Request(url, method="GET", headers={"User-Agent": "fatsecret-app/1.0"})
        with request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            return self._parse_form_response(body)

    def _upsert_env_var(self, key: str, value: str) -> None:
        env_path = Path(".env")
        if not env_path.exists():
            env_path.write_text(f"{key}={value}\n", encoding="utf-8")
            return

        lines = env_path.read_text(encoding="utf-8").splitlines()
        updated = False
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                updated = True
                break
        if not updated:
            lines.append(f"{key}={value}")
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _obtain_oauth1_user_tokens(self) -> bool:
        """OAuth1 flow: request_token -> authorize -> access_token."""
        print("\n📋 Введите response string от request_token (в формате:")
        print("   oauth_callback_confirmed=true&oauth_token=XXX&oauth_token_secret=YYY)")
        print("   Получить её можно через Postman/браузер, минуя Cloudflare.")

        req_token_input = ""
        try:
            req_token_input = input("> ").strip()
        except EOFError:
            print("❌ Не удалось прочитать ввод.")
            return False

        if not req_token_input:
            print("❌ Пустой ввод.")
            return False

        req_token_data = self._parse_form_response(req_token_input)
        if not req_token_data.get("oauth_token") or not req_token_data.get("oauth_token_secret"):
            print(f"❌ Не удалось распознать request_token из ввода: {req_token_data}")
            return False

        request_token = req_token_data.get("oauth_token", "")
        request_token_secret = req_token_data.get("oauth_token_secret", "")

        auth_url = f"{self.OAUTH_AUTHORIZE_URL}?{parse.urlencode({'oauth_token': request_token})}"
        print("\n🔗 Откройте ссылку и подтвердите доступ FatSecret:")
        print(auth_url)

        verifier = self.oauth_verifier.strip()
        if not verifier:
            try:
                verifier = input("Введите oauth_verifier (PIN) из FatSecret: ").strip()
            except EOFError:
                print("❌ Не удалось прочитать oauth_verifier из stdin.")
                print("   Укажите FATSECRET_OAUTH_VERIFIER в .env и запустите снова.")
                return False

        if not verifier:
            print("❌ oauth_verifier пустой")
            return False

        access_data = None
        access_error = None
        for flow_name, flow_fn in [
            (
                "POST",
                lambda: self._oauth1_post_form(
                    self.OAUTH_ACCESS_TOKEN_URL,
                    token=request_token,
                    token_secret=request_token_secret,
                    extra_oauth_params={"oauth_verifier": verifier},
                ),
            ),
            (
                "GET",
                lambda: self._oauth1_get_form(
                    self.OAUTH_ACCESS_TOKEN_URL,
                    token=request_token,
                    token_secret=request_token_secret,
                    extra_oauth_params={"oauth_verifier": verifier},
                ),
            ),
        ]:
            try:
                access_data = flow_fn()
                if access_data:
                    break
            except HTTPError as e:
                body = e.read().decode("utf-8", errors="ignore") if hasattr(e, "read") else ""
                access_error = f"{flow_name} HTTP {e.code}: {body[:500] or str(e)}"
            except Exception as e:
                access_error = f"{flow_name}: {e}"

        if not access_data:
            print(f"❌ Failed to exchange access token: {access_error or 'unknown error'}")
            return False

        access_token = access_data.get("oauth_token", "")
        access_token_secret = access_data.get("oauth_token_secret", "")
        if not access_token or not access_token_secret:
            print(f"❌ Invalid access token response: {access_data}")
            return False

        self.oauth_token = access_token
        self.oauth_token_secret = access_token_secret

        self._upsert_env_var("FATSECRET_OAUTH_TOKEN", access_token)
        self._upsert_env_var("FATSECRET_OAUTH_TOKEN_SECRET", access_token_secret)
        print("✓ OAuth v1 user token saved to .env")
        return True

    def ensure_auth(self) -> bool:
        """Prefer OAuth1 credentials for FatSecret API calls."""
        if self.oauth_consumer_key and self.oauth_consumer_secret:
            print("✓ Using OAuth v1 credentials")
            if self.oauth_token and not self.oauth_token_secret:
                print("⚠ FATSECRET_OAUTH_TOKEN set without FATSECRET_OAUTH_TOKEN_SECRET")
            if self.oauth_token_secret and not self.oauth_token:
                print("⚠ FATSECRET_OAUTH_TOKEN_SECRET set without FATSECRET_OAUTH_TOKEN")
            if self.oauth_token and self.oauth_token_secret:
                # Test tokens before using them
                if self._test_oauth1_tokens():
                    return True
                # Tokens invalid - clear them and re-authorize
                print("⚠ Existing OAuth tokens invalid or expired, starting fresh authorization...")
                self.oauth_token = ""
                self.oauth_token_secret = ""
                self._upsert_env_var("FATSECRET_OAUTH_TOKEN", "")
                self._upsert_env_var("FATSECRET_OAUTH_TOKEN_SECRET", "")

            print("ℹ Starting OAuth v1 authorization flow...")
            return self._obtain_oauth1_user_tokens()

        if self.access_token:
            print("✓ OAuth v1 creds not set, using FATSECRET_ACCESS_TOKEN fallback")
            return True

        print("❌ Missing OAuth v1 credentials")
        print("   Set FATSECRET_CONSUMER_KEY and FATSECRET_CONSUMER_SECRET (from FatSecret OAuth1 app)")
        print("   (optional: FATSECRET_OAUTH_TOKEN / FATSECRET_OAUTH_TOKEN_SECRET)")
        print("   FATSECRET_CLIENT_ID/SECRET are OAuth2 and may cause invalid OAuth1 signature")
        return False

    def call_api(self, method: str, **kwargs):
        params = {
            "method": method,
            "format": "json",
            **kwargs,
        }
        if self.oauth_consumer_key and self.oauth_consumer_secret:
            return self._oauth1_get(self.API_URL, params)

        url = f"{self.API_URL}?{parse.urlencode(params, quote_via=parse.quote)}"
        req = request.Request(url, headers={"Authorization": f"Bearer {self.access_token}"}, method="GET")
        with request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))

    def _test_oauth1_tokens(self) -> bool:
        """Test if current OAuth1 tokens are valid by calling a simple API."""
        try:
            result = self._oauth1_get(self.API_URL, {"method": "profile", "format": "json"})
            if isinstance(result, dict):
                if "error" in result:
                    error_code = result.get("error", {}).get("code")
                    if error_code in (9, 8):
                        print(f"⚠ OAuth token invalid (error {error_code}), need to re-authorize")
                        return False
                elif "profile" in result or "profiles" in result:
                    print("✓ OAuth tokens are valid")
                    return True
            return False
        except Exception as e:
            print(f"⚠ Token test failed: {e}")
            return False

    @staticmethod
    def _to_days_since_epoch(day: date) -> int:
        return (day - date(1970, 1, 1)).days

    def call_food_entries_v2(self, day: date, food_entry_id: str | None = None):
        """Call GET /rest/food-entries/v2 according to FatSecret v2 docs."""
        params = {
            "method": "food_entries.get.v2",
            "date": self._to_days_since_epoch(day),
            "format": "json",
        }
        if food_entry_id:
            params["food_entry_id"] = str(food_entry_id)

        try:
            if self.oauth_consumer_key and self.oauth_consumer_secret:
                return self._oauth1_get(self.API_V2_FOOD_ENTRIES_URL, params)

            url = f"{self.API_V2_FOOD_ENTRIES_URL}?{parse.urlencode(params, quote_via=parse.quote)}"
            req = request.Request(
                url,
                headers={"Authorization": f"Bearer {self.access_token}"},
                method="GET",
            )
            with request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8", errors="ignore"))
        except HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore") if hasattr(e, "read") else ""
            return {"error": {"code": e.code, "message": body[:500] or str(e)}}
        except Exception as e:
            return {"error": {"code": "exception", "message": str(e)}}

    @staticmethod
    def _to_float(value) -> float:
        """Convert api numeric fields to float safely."""
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)

        value_str = str(value).strip().replace(",", ".")
        if not value_str:
            return 0.0

        try:
            return float(value_str)
        except ValueError:
            return 0.0

    def get_day_calories(self, day: date):
        """Return total calories for one day and optional error."""
        data = self.call_food_entries_v2(day, self.food_entry_id)

        if isinstance(data, dict) and data.get("error"):
            return 0.0, 0, data.get("error")

        # For this endpoint response can be one entry or a wrapper; support both.
        entries = []
        if isinstance(data, dict):
            if isinstance(data.get("food_entry"), dict):
                entries = [data.get("food_entry")]
            elif isinstance(data.get("food_entries"), dict):
                entries = data.get("food_entries", {}).get("food_entry", [])

        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list):
            entries = []

        total = 0.0
        for entry in entries:
            if not isinstance(entry, dict):
                continue

            calories = (
                entry.get("calories")
                or entry.get("kcal")
                or entry.get("energy")
                or 0
            )
            total += self._to_float(calories)

        return total, len(entries), None

    def show_last_3_days_calories(self) -> None:
        print("\nCALORIES FOR LAST 3 DAYS")
        print("=" * 55)
        print(f"Endpoint: {self.API_V2_FOOD_ENTRIES_URL}")
        if self.food_entry_id:
            print(f"Using FATSECRET_FOOD_ENTRY_ID={self.food_entry_id}")
        else:
            print("FATSECRET_FOOD_ENTRY_ID is not set, sending only date")

        total_3_days = 0.0
        today = date.today()

        for shift in range(2, -1, -1):
            day = today - timedelta(days=shift)
            day_total, entries_count, error = self.get_day_calories(day)

            if error:
                print(f"{day.isoformat()}: error {error.get('code')} - {error.get('message')}. ERROR:{error}")
                continue

            total_3_days += day_total
            print(
                f"{day.isoformat()}: {day_total:.0f} kcal "
                f"(entries: {entries_count})"
            )

        print("-" * 55)
        print(f"Total for last 3 days: {total_3_days:.0f} kcal")

    def run(self) -> None:
        print("=" * 55)
        print(" FATSECRET ACCOUNT DATA")
        print("=" * 55)

        self.load_config()
        if not self.ensure_auth():
            sys.exit(1)

        try:
            self.show_last_3_days_calories()
        except Exception as e:
            print(f"  ⚠ Failed to calculate calories for last 3 days: {e}")

        print("\n✓ Done")


def main() -> None:
    app = FatSecretApp()
    app.run()


if __name__ == "__main__":
    main()

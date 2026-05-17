#!/usr/bin/env python3
"""Скрипт для OAuth авторизации FatSecret"""

import os
import sys
import codecs

# UTF-8 for Windows
if sys.platform == 'win32':
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

from pathlib import Path
from dotenv import load_dotenv
from fatsecret import Fatsecret

# Load .env
env_path = Path(__file__).parent / '.env'
load_dotenv(env_path)

CONSUMER_KEY = os.getenv('FATSECRET_CONSUMER_KEY')
CONSUMER_SECRET = os.getenv('FATSECRET_CONSUMER_SECRET')

print("=" * 50)
print("  FATSECRET OAuth AUTHORIZATION")
print("=" * 50)

fs = Fatsecret(CONSUMER_KEY, CONSUMER_SECRET)

print("\n1. Open this URL in your browser:")
auth_url = fs.get_authorize_url()
print(auth_url)
print()

print("2. Login and copy the PIN code")
pin = input("Enter PIN: ").strip()

print()
print("3. Getting access tokens...")
access_token, access_secret = fs.authenticate(pin)

print()
print("=" * 50)
print("SUCCESS! Add these to .env file:")
print("=" * 50)
print(f'FATSECRET_OAUTH_TOKEN={access_token}')
print(f'FATSECRET_OAUTH_TOKEN_SECRET={access_secret}')
print()
print("Or update .env file automatically? (y/n)")
answer = input("> ").strip().lower()
if answer == 'y':
    env_file = Path(__file__).parent / '.env'
    content = env_file.read_text(encoding='utf-8')
    
    # Update or add tokens
    lines = content.split('\n')
    new_lines = []
    token_updated = False
    secret_updated = False
    
    for line in lines:
        if line.startswith('FATSECRET_OAUTH_TOKEN='):
            new_lines.append(f'FATSECRET_OAUTH_TOKEN={access_token}')
            token_updated = True
        elif line.startswith('FATSECRET_OAUTH_TOKEN_SECRET='):
            new_lines.append(f'FATSECRET_OAUTH_TOKEN_SECRET={access_secret}')
            secret_updated = True
        else:
            new_lines.append(line)
    
    if not token_updated:
        new_lines.append(f'FATSECRET_OAUTH_TOKEN={access_token}')
    if not secret_updated:
        new_lines.append(f'FATSECRET_OAUTH_TOKEN_SECRET={access_secret}')
    
    env_file.write_text('\n'.join(new_lines), encoding='utf-8')
    print(".env file updated!")
else:
    print("Please update .env manually.")
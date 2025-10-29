"""
PropertyConnect AI Chatbot — Telegram + Anthropic + NetSuite
-------------------------------------------------------------
A fully integrated bot that connects Telegram to Claude AI
and NetSuite custom records (PropertyConnect).
"""

import telebot
import requests
import json
import webbrowser
import uuid
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from requests.auth import HTTPBasicAuth
import re
from dotenv import load_dotenv
import os


load_dotenv()


# ========================
# CONFIGURATION
# ========================
BOT_NAME = "Property Connect AI"

# Telegram Bot Token (from @BotFather)
TG_API_TOKEN = os.getenv("TG_API_TOKEN")

# Anthropic API Key
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# NetSuite OAuth2 Config
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")

REDIRECT_URI = "http://localhost:8080"
AUTH_URL = "https://3580073.app.netsuite.com/app/login/oauth2/authorize.nl"
TOKEN_URL = "https://3580073.suitetalk.api.netsuite.com/services/rest/auth/oauth2/v1/token"
SCOPE = "rest_webservices"

# Script ID of the custom record
CUSTOM_RECORD_SCRIPT_ID = "customrecord_collab_properties"

# ========================
# TELEGRAM BOT INIT
# ========================
bot = telebot.TeleBot(TG_API_TOKEN)

# ========================
# ANTHROPIC HELPER
# ========================
def ask_claude(prompt: str) -> str:
    """Send a message to Claude and return the response."""
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "content-type": "application/json",
        "anthropic-version": "2023-06-01"
    }
    data = {
        "model": "claude-sonnet-4-5-20250929",
        "max_tokens": 500,
        "messages": [{"role": "user", "content": prompt}]
    }
    try:
        response = requests.post(url, headers=headers, data=json.dumps(data))
        response.raise_for_status()
        result = response.json()
        return result["content"][0]["text"] if "content" in result else "No response from Claude."
    except Exception as e:
        return f"⚠️ Error contacting Claude: {e}"

# ========================
# USER INPUT PARSER
# ========================
def extract_filters(user_text):
    """Extract limit, sort_by, location, max_price from user message."""
    filters = {}
    limit = None
    sort_by = None

    # Top N
    top_match = re.search(r'top (\d+)', user_text, re.IGNORECASE)
    if top_match:
        limit = int(top_match.group(1))

    # Affordable → sort by price
    if "affordable" in user_text.lower():
        sort_by = "price"

    # Max price
    price_match = re.search(r'\$\s?([\d,]+)', user_text)
    if price_match:
        filters["max_price"] = float(price_match.group(1).replace(",", ""))

    # Location detection
    loc_match = re.search(r'in ([A-Za-z\s]+)', user_text)
    if loc_match:
        filters["location"] = loc_match.group(1).strip()

    return filters, limit, sort_by

# ========================
# NETSUITE OAUTH2 HELPERS
# ========================
STATE = str(uuid.uuid4())
auth_request_url = (
    f"{AUTH_URL}?response_type=code"
    f"&client_id={CLIENT_ID}"
    f"&redirect_uri={REDIRECT_URI}"
    f"&scope={SCOPE}"
    f"&state={STATE}"
)

class OAuthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = urlparse(self.path).query
        params = parse_qs(query)
        if "code" in params and "state" in params:
            self.server.auth_code = params["code"][0]
            self.server.state_received = params["state"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Authorization code received! You can close this window.</h2>")
        else:
            self.send_response(400)
            self.end_headers()

def get_auth_code():
    server_address = ('', 8080)
    httpd = HTTPServer(server_address, OAuthHandler)
    httpd.auth_code = None
    httpd.state_received = None

    print("Opening browser for NetSuite login...")
    webbrowser.open(auth_request_url)
    print("Waiting for authorization code...")

    while httpd.auth_code is None:
        httpd.handle_request()

    if httpd.state_received != STATE:
        raise ValueError("State mismatch! Potential CSRF attack.")

    return httpd.auth_code

def exchange_code_for_token(auth_code):
    data = {"grant_type": "authorization_code", "code": auth_code, "redirect_uri": REDIRECT_URI}
    response = requests.post(TOKEN_URL, data=data, auth=HTTPBasicAuth(CLIENT_ID, CLIENT_SECRET))
    response.raise_for_status()
    token_data = response.json()
    token_data["expires_at"] = time.time() + float(token_data.get("expires_in", 3600))
    return token_data

def refresh_access_token(refresh_token):
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    response = requests.post(TOKEN_URL, data=data, auth=HTTPBasicAuth(CLIENT_ID, CLIENT_SECRET))
    response.raise_for_status()
    token_data = response.json()
    token_data["expires_at"] = time.time() + float(token_data.get("expires_in", 3600))
    return token_data

def get_valid_access_token(token_data):
    if time.time() > token_data.get("expires_at", 0):
        print("Refreshing expired access token...")
        token_data = refresh_access_token(token_data["refresh_token"])
    return token_data

# ========================
# NETSUITE FETCH PROPERTIES
# ========================
def get_netsuite_properties(access_token, filters=None, limit=5, sort_by=None):
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    url = f"https://3580073.suitetalk.api.netsuite.com/services/rest/record/v1/{CUSTOM_RECORD_SCRIPT_ID}"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    data = response.json()

    items = data.get("items", [])
    properties = []

    for item in items:
        values = item.get("values", {})
        prop_name = values.get("custrecord_collab_prop_name", "N/A")
        prop_location = values.get("custrecord_collab_prop_loc", "N/A")
        base_price = values.get("custrecord_collab_prop_baseprice", 0)
        area = values.get("custrecord_collab_prop_area", "N/A")
        bedrooms = values.get("custrecord_collab_prop_bedrooms", 0)
        bathroom = values.get("custrecord_collab_prop_bathroom", 0)

        # Apply filters
        if filters:
            if "max_price" in filters and base_price > filters["max_price"]:
                continue
            if "location" in filters and filters["location"].lower() not in prop_location.lower():
                continue

        properties.append({
            "name": prop_name,
            "location": prop_location,
            "price": base_price,
            "area": area,
            "bedrooms": bedrooms,
            "bathroom": bathroom
        })

    # Sort
    if sort_by == "price":
        properties.sort(key=lambda x: x["price"])

    # Limit
    return properties[:limit]

# ========================
# TELEGRAM BOT HANDLERS
# ========================
@bot.message_handler(commands=['start'])
def welcome(message):
    bot.send_message(message.chat.id,
                     f"🤖 Welcome to {BOT_NAME}!\nAsk me anything about available properties.")

@bot.message_handler(func=lambda m: True)
def chat_with_ai(message):
    user_text = message.text
    bot.send_chat_action(message.chat.id, 'typing')

    filters, limit, sort_by = extract_filters(user_text)
    access_token = get_valid_access_token(tokens)["access_token"]

    props = get_netsuite_properties(access_token, filters=filters, limit=limit or 5, sort_by=sort_by)

    if props:
        reply_text = "🏡 Here are the top properties:\n"
        for p in props:
            reply_text += f"- {p['name']} ({p['location']}): ${p['price']}, {p['area']} sqm, {p['bedrooms']}BR/{p['bathroom']}BA\n"
    else:
        reply_text = "❌ No properties found matching your criteria."

    bot.send_message(message.chat.id, reply_text)

# ========================
# MAIN
# ========================
if __name__ == "__main__":
    # Get NetSuite access token
    code = get_auth_code()
    tokens = exchange_code_for_token(code)
    print("✅ NetSuite access token obtained.")

    # Start Telegram bot
    print("🚀 Bot is running... Press Ctrl+C to stop.")
    bot.polling(non_stop=True)
import telebot
import requests
import json
import webbrowser
import uuid
import time
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from requests.auth import HTTPBasicAuth

# ========================
# CONFIGURATION
# ========================
BOT_NAME = "Property Connect AI"
TG_API_TOKEN = "8235006819:AAHAMJJLkwsQV5VHeyvXNqfSafYJAwaisjw"

# NetSuite OAuth2 Config
CLIENT_ID = "8527639950b0865a1ba5635aa6f280d08702bc80328708fa83bda5b099ab80d1"
CLIENT_SECRET = "9b51386c62737ea48ea6d8d7d0b2aecb0990577f5f856ebdb8d9bd8c9a8ce871"
REDIRECT_URI = "http://localhost:8080"
AUTH_URL = "https://3580073.app.netsuite.com/app/login/oauth2/authorize.nl"
TOKEN_URL = "https://3580073.suitetalk.api.netsuite.com/services/rest/auth/oauth2/v1/token"
CUSTOM_RECORD_SCRIPT_ID = "customrecord_collab_properties"
SCOPE = "rest_webservices"

print("hELLO")

# ========================
# TELEGRAM BOT INIT
# ========================
bot = telebot.TeleBot(TG_API_TOKEN)

# ========================
# USER INPUT PARSER
# ========================
def extract_filters(user_text):
    filters = {}
    limit = None
    sort_by = None

    top_match = re.search(r'top (\d+)', user_text, re.IGNORECASE)
    if top_match:
        limit = int(top_match.group(1))

    if "affordable" in user_text.lower():
        sort_by = "price"

    price_match = re.search(r'\$\s?([\d,]+)', user_text)
    if price_match:
        filters["max_price"] = float(price_match.group(1).replace(",", ""))

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
    webbrowser.open(auth_request_url)

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
        token_data = refresh_access_token(token_data["refresh_token"])
    return token_data

# ========================
# NETSUITE FETCH PROPERTIES
# ========================
def get_netsuite_properties(access_token, filters=None, limit=5, sort_by=None):
    ACCOUNT_ID = "3580073"  # Replace with your NetSuite account ID
    url = f"https://{ACCOUNT_ID}.suitetalk.api.netsuite.com/services/rest/record/v1/{CUSTOM_RECORD_SCRIPT_ID}"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    response = requests.get(url, headers=headers)
    response.raise_for_status()
    data = response.json()

    properties = []
    for item in data.get("items", []):
        values = item.get("values", {})
        price = values.get("custrecord_collab_prop_baseprice", 0)
        location = values.get("custrecord_collab_prop_loc", "N/A")

        if filters:
            if "max_price" in filters and price > filters["max_price"]:
                continue
            if "location" in filters and filters["location"].lower() not in location.lower():
                continue

        properties.append({
            "name": values.get("custrecord_collab_prop_name", "N/A"),
            "location": location,
            "price": price,
            "area": values.get("custrecord_collab_prop_area", "N/A"),
            "bedrooms": values.get("custrecord_collab_prop_bedrooms", 0),
            "bathroom": values.get("custrecord_collab_prop_bathroom", 0)
        })

    if sort_by == "price":
        properties.sort(key=lambda x: x["price"])

    return properties[:limit]

# ========================
# TELEGRAM BOT HANDLERS
# ========================
@bot.message_handler(commands=['start'])
def welcome(message):
    bot.send_message(message.chat.id, f"🤖 Welcome to {BOT_NAME}! Ask me about available properties.")

@bot.message_handler(func=lambda m: True)
def chat_with_user(message):
    user_text = message.text
    bot.send_chat_action(message.chat.id, 'typing')

    filters, limit, sort_by = extract_filters(user_text)
    access_token = get_valid_access_token(tokens)["access_token"]
    props = get_netsuite_properties(access_token, filters=filters, limit=limit or 5, sort_by=sort_by)

    if props:
        reply = "🏡 Here are the top properties:\n"
        for p in props:
            reply += f"- {p['name']} ({p['location']}): ${p['price']}, {p['area']} sqm, {p['bedrooms']}BR/{p['bathroom']}BA\n"
    else:
        reply = "❌ No properties found matching your criteria."

    bot.send_message(message.chat.id, reply)

# ========================
# MAIN
# ========================
if __name__ == "__main__":
    # Get NetSuite access token
    code = get_auth_code()
    tokens = exchange_code_for_token(code)
    print("✅ NetSuite access token obtained.")

    # Start Telegram bot
    print("🚀 Bot is running...")
    bot.polling(non_stop=True)

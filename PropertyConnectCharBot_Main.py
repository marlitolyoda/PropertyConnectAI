"""
PropertyConnect AI Chatbot — Telegram + Anthropic + NetSuite MCP
----------------------------------------------------------------
Claude can now use NetSuite MCP tools (ns_getRecord, ns_runSavedSearch, etc.)
via the access token obtained from OAuth2.
"""

import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
import telebot
import requests
import json
import webbrowser
import uuid
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from requests.auth import HTTPBasicAuth
from anthropic import Anthropic
import os
from dotenv import load_dotenv

# ========================
# CONFIGURATION
# ========================
load_dotenv()
BOT_NAME = "Property Connect AI"

TG_API_TOKEN = os.getenv("TG_API_TOKEN_MAIN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)

# NetSuite OAuth2 Config
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")

ACCOUNT_ID = "3580073"
REDIRECT_URI = "http://localhost:8080"
AUTH_URL = f"https://{ACCOUNT_ID}.app.netsuite.com/app/login/oauth2/authorize.nl"
TOKEN_URL = f"https://{ACCOUNT_ID}.suitetalk.api.netsuite.com/services/rest/auth/oauth2/v1/token"
MCP_URL = f"https://{ACCOUNT_ID}.app.netsuite.com/mcp"   # <-- MCP endpoint base
SCOPE = "rest_webservices"

# ========================
# INIT
# ========================
bot = telebot.TeleBot(TG_API_TOKEN)

user_conversations = {}
# ========================
# STEP 1: OAUTH2 AUTH FLOW
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
            self.wfile.write(b"<h2> Authorization received! You can close this window.</h2>")
        else:
            self.send_response(400)
            self.end_headers()

def get_auth_code():
    server_address = ('', 8080)
    httpd = HTTPServer(server_address, OAuthHandler)
    httpd.auth_code = None
    httpd.state_received = None

    print("🌐 Opening browser for NetSuite login...")
    webbrowser.open(auth_request_url)

    while httpd.auth_code is None:
        httpd.handle_request()

    if httpd.state_received != STATE:
        raise ValueError("⚠️ State mismatch — possible CSRF attack!")

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
        print("🔄 Refreshing expired token...")
        token_data = refresh_access_token(token_data["refresh_token"])
    return token_data

# ========================
# STEP 2: ANTHROPIC + MCP
# ========================
def get_anthropic_client_with_mcp(_):
    return Anthropic(api_key=ANTHROPIC_API_KEY)

def handle_mcp_function_call(tool_name, params, access_token):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Prefer": "transient"
    }

    base_url = "https://3580073.suitetalk.api.netsuite.com/services/rest/record/v1"
    mcp_base = f"https://3580073.app.netsuite.com/mcp"

    if tool_name == "ns_listRecords":
        record_type = params.get("recordType")
        url = f"{base_url}/{record_type}"
        response = requests.get(url, headers=headers)
        data = response.json()

        # Fetch details for each record
        records = []
        for item in data.get("items", []):
            record_url = item["links"][0]["href"]
            record_resp = requests.get(record_url, headers=headers)
            if record_resp.ok:
                record_data = record_resp.json()
                records.append(record_data)
            else:
                records.append({"id": item.get("id"), "error": "Failed to fetch record details"})

        return {
            "count": len(records),
            "records": records
        }

    elif tool_name == "ns_getRecord":
        record_type = params.get("recordType")
        record_id = params.get("recordId")
        url = f"{base_url}/{record_type}/{record_id}"
        response = requests.get(url, headers=headers)
        return response.json()

    # === Tool: Create a record ===
    elif tool_name == "ns_createRecord":
        record_type = params.get("recordType")
        fields = params.get("fields", {})
        url = f"{base_url}/{record_type}"
        response = requests.post(url, headers=headers, json=fields)

        try:
            data = response.json() if response.text else {}
        except ValueError:
            data = {}

        if response.ok:
            # NetSuite often returns a Location header even when JSON is empty
            if not data and "Location" in response.headers:
                data = {"location": response.headers["Location"]}
            return {"status": response.status_code, "data": data}
        else:
            return {
                "error": f"Failed to create record ({response.status_code})",
                "details": response.text or "No response body"
            }

    # === Tool: Update a record ===
    elif tool_name == "ns_updateRecord":
        record_type = params.get("recordType")
        record_id = params.get("recordId")
        fields = params.get("fields", {})
        url = f"{base_url}/{record_type}/{record_id}"
        response = requests.patch(url, headers=headers, json=fields)
        if response.ok:
            return response.json()
        else:
            return {"error": "Failed to update record", "details": response.text}

    # === Tool: Get record type metadata ===
    elif tool_name == "ns_getRecordTypeMetadata":
        record_type = params.get("recordType")
        url = f"{mcp_base}/rest/metadata-catalog/v1/recordTypes/{record_type}"
        response = requests.get(url, headers=headers)
        if response.ok:
            return response.json()
        else:
            return {"error": f"Failed to fetch metadata for {record_type}", "details": response.text}

    # === Tool: List all saved searches ===
    elif tool_name == "ns_listSavedSearches":
        url = f"{mcp_base}/rest/record/v1/savedSearch"
        response = requests.get(url, headers=headers)
        if response.ok:
            return response.json()
        else:
            return {"error": "Failed to list saved searches", "details": response.text}

    # === Tool: Run a saved search (MCP) ===
    elif tool_name == "ns_runSavedSearch":
        saved_search_id = params.get("searchId")
        url = f"{base_url}/rest/record/v1/savedSearch/{saved_search_id}/results"
        response = requests.get(url, headers=headers)
        if response.ok:
            return response.json()
        else:
            return {"error": f"Failed to run saved search {saved_search_id}", "details": response.text}

    # === Tool: Run a report (MCP) ===
    elif tool_name == "ns_runReport":
        report_id = params.get("reportId")
        payload = {"parameters": params.get("parameters", {})}
        url = f"{base_url}/rest/reporting/v1/reports/{report_id}/run"
        response = requests.post(url, headers=headers, json=payload)
        if response.ok:
            return response.json()
        else:
            return {"error": f"Failed to run report {report_id}", "details": response.text}

    # === Tool: List all reports ===
    elif tool_name == "ns_listAllReports":
        url = f"{mcp_base}/rest/reporting/v1/reports"
        response = requests.get(url, headers=headers)
        if response.ok:
            return response.json()
        else:
            return {"error": "Failed to list reports", "details": response.text}

    # === Tool: Run a custom SuiteQL query (MCP) ===
    elif tool_name == "ns_runCustomSuiteQL":
        sql = params.get("sqlQuery")
        payload = {"q": sql}
        url = f"{base_url}/rest/query/v1/suiteql"
        response = requests.post(url, headers=headers, json=payload)
        if response.ok:
            return response.json()
        else:
            return {"error": f"Failed to run SuiteQL query", "details": response.text}

    else:
        return {"error": f"Unknown tool name: {tool_name}"}

def ask_claude_with_mcp(messages, access_token):

    while True:
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=700,
            system="""
                    Role:
                    You are CollabAnts Property Assistant, an intelligent real estate sales agent integrated with NetSuite.
                    Your job is to help users discover, compare, and inquire about properties that match their preferences.

                    Main tasks:
                    1. Understand user preferences (location, price range, size, bedrooms, etc.).
                    2. Fetch and filter property data from NetSuite.
                    3. Recommend best-matching properties and provide details.
                    4. Maintain a friendly, persuasive, and professional tone.
                    5. Don't answer if the question is not related in the role you have.
                    6. Before providing any quotation or proposal, first confirm:
                        - The user’s **contact number** or **email address**, or
                        - Whether the user intends to **proceed with availing the property**.
                    7. Once both contact number and email are obtained, create a record type named Lead in NetSuite with the following details:
                        - Custom Form: "Standard Lead Form"
                        - Lead Status: "Lead - Qualified"
                        - Type: "Individual"
                        - Primary Subsidiary: "Collab Ants"
                        - Include the user's name, contact number, and email address
                    8. Before creating the Lead record, first check whether the phone number already exists.
                        - If the phone number does **not** exist → proceed with creating the record.
                        - If it **does** exist → do **not** create a duplicate entry.
                    9. Check if the phone no or email already exists in the customer list
                    

                    HARD RULE:
                    1. Focus only on active properties (isInactive: false).
                    2. Don't show the property ID
                    3. Telegram has a 4096 character limit. Maximize your response in the character limit of Telegram
                    4. Always use ns_updateRecord to add new info (email, phone, etc.) based on Telegram chat id.
                    5. If the user requests “all properties,” first ask for specific criteria (e.g., location, budget, or property type) to prevent performance issues.
                    6. Limit always to Top 3 properties when providing property details to the user.

                    Record Used:
                    1. customrecord_collab_properties 
                    """,
            tools=[
                {
                    "name": "ns_getRecord",
                    "description": "Get a specific NetSuite record by type and internal ID.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "recordType": {"type": "string"},
                            "recordId": {"type": "string"}
                        },
                        "required": ["recordType", "recordId"]
                    }
                },
                {
                    "name": "ns_listRecords",
                    "description": "List all records of a given type from NetSuite, including details.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "recordType": {"type": "string"}
                        },
                        "required": ["recordType"]
                    }
                },
                {
                    "name": "ns_createRecord",
                    "description": "Create a new record in NetSuite for the given record type and field values.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "recordType": {"type": "string"},
                            "fields": {"type": "object"}
                        },
                        "required": ["recordType", "fields"]
                    }
                },
                {
                    "name": "ns_updateRecord",
                    "description": "Update an existing NetSuite record with new field values.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "recordType": {"type": "string"},
                            "recordId": {"type": "string"},
                            "fields": {"type": "object"}
                        },
                        "required": ["recordType", "recordId", "fields"]
                    }
                },
                {
                    "name": "ns_getRecordTypeMetadata",
                    "description": "Retrieve metadata for a specific NetSuite record type (fields, sublists, etc.).",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "recordType": {"type": "string"}
                        },
                        "required": ["recordType"]
                    }
                },
                {
                    "name": "ns_listSavedSearches",
                    "description": "List all available saved searches from NetSuite.",
                    "input_schema": {
                        "type": "object",
                        "properties": {}
                    }
                },
                {
                    "name": "ns_runSavedSearch",
                    "description": "Run a saved search in NetSuite and return its results.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "searchId": {"type": "string"}
                        },
                        "required": ["searchId"]
                    }
                },
                {
                    "name": "ns_listAllReports",
                    "description": "List all available standard and custom reports from NetSuite.",
                    "input_schema": {
                        "type": "object",
                        "properties": {}
                    }
                },
                {
                    "name": "ns_runReport",
                    "description": "Run a NetSuite report and return the data.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "reportId": {"type": "string"},
                            "parameters": {"type": "object"}
                        },
                        "required": ["reportId"]
                    }
                },
                {
                    "name": "ns_runCustomSuiteQL",
                    "description": "Execute a custom SuiteQL query in NetSuite and return the results.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "sqlQuery": {"type": "string"}
                        },
                        "required": ["sqlQuery"]
                    }
                }                
            ],
            messages=messages
        )

        # Find if Claude requested a tool
        tool_block = None
        for block in response.content:
            if hasattr(block, "type") and block.type == "tool_use":
                tool_block = block
                break

        if not tool_block:
            # Claude gave a final answer
            return "".join([
                part.text for part in response.content
                if hasattr(part, "text")
            ])

        # Execute the tool call
        tool_name = tool_block.name
        params = tool_block.input
        tool_result = handle_mcp_function_call(tool_name, params, access_token)

        # Append both Claude's tool call and our result into the conversation
        messages.append({"role": "assistant", "content": response.content})
        messages.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_block.id,
                "content": json.dumps(tool_result, indent=2)
            }]
        })

        # Continue the loop (Claude will now see the result and proceed)

# ========================
# 🆕 STEP 3: AUTO-CREATE LEAD UPON GETTING PHONE NO OR EMAIL
# ========================
def create_lead_in_netsuite(user_id, user_data, access_token):
    """Create a Lead record in NetSuite once both phone + email are known."""
    first_name = user_data.get("first_name", "Unknown")
    last_name = user_data.get("last_name", "User")
    phone = user_data.get("phone")
    email = user_data.get("email")

    if not (phone and email):
        print(f"⚠️ Skipping lead creation — missing data. Phone: {phone}, Email: {email}")
        return
    
    # 🕵️ Check for existing lead first
    existing = lead_exists_in_netsuite(phone, email, access_token)
    if existing:
        print(f"🚫 Lead already exists: ID {existing.get('id')}")
        return {"existing": existing.get("id")}


    print(f"🆕 Creating Lead for {first_name} ({phone}, {email})")

    payload = {
        "recordType": "customer",
        "fields": {
            "customform": {"id": "221"},      # ✅ Standard Lead Form internal ID
            "entitystatus": {"id": "7"},      # ✅ Lead - Qualified
            "subsidiary": {"id": "10"},       # ✅ Collab Ants
            "isperson": True,
            "firstname": first_name,
            "lastname": last_name,
            "phone": phone,
            "email": email
        }
    }

    creation = handle_mcp_function_call("ns_createRecord", payload, access_token)
    print("✅ Lead created in NetSuite:", creation)
    return creation

# ========================
# 🕵️ CHECK IF LEAD EXISTS
# ========================
def lead_exists_in_netsuite(phone, email, access_token):
    """Check if a lead already exists using phone or email."""
    filters = []
    if phone:
        filters.append(f"phone = '{phone}'")
    if email:
        filters.append(f"email = '{email}'")

    if not filters:
        return False  # Nothing to search for

    where_clause = " OR ".join(filters)
    sql = f"SELECT id, entityid, email, phone FROM customer WHERE {where_clause}"

    result = handle_mcp_function_call("ns_runCustomSuiteQL", {"sqlQuery": sql}, access_token)
    records = result.get("items") or result.get("rows") or []

    if records:
        existing = records[0]
        print(f"ℹ️ Existing lead found: {existing}")
        return existing
    else:
        print("🆕 No existing lead found.")
        return False
    

# ========================
# TELEGRAM HANDLERS
# ========================
@bot.message_handler(commands=['start'])
def welcome(message):
    bot.send_chat_action(message.chat.id, 'typing')
    valid_token = get_valid_access_token(tokens)
    access_token = valid_token["access_token"]

    # Initialize conversation storage
    user_id = message.chat.id
    user_conversations[user_id] = {
        "chat_history": [],
        "first_name": message.from_user.first_name,
        "last_name": message.from_user.last_name,
        "phone": None,
        "email": None,
        "lead_created": False
    }

    bot.send_message(
        user_id,
        f"👋 Hi {message.from_user.first_name}, welcome to {BOT_NAME}!\n"
        f"Tell me what kind of property you’re looking for 🏡",
        parse_mode="Markdown"
    )

@bot.message_handler(func=lambda m: True)
def chat_with_ai(message):
    global tokens
    bot.send_chat_action(message.chat.id, 'typing')
    valid_token = get_valid_access_token(tokens)
    access_token = valid_token["access_token"]

    user_id = message.chat.id
    if user_id not in user_conversations:
        user_conversations[user_id] = {"chat_history": [], "phone": None, "email": None, "lead_created": False}

    conversation = user_conversations[user_id]["chat_history"]
    user_data = user_conversations[user_id]

    text = message.text.strip()
    conversation.append({"role": "user", "content": text})

    # 🧠 Simple pattern-based extraction for phone/email
    import re
    phone_match = re.search(r'(\+?\d{10,15})', text)
    email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)

    if phone_match and not user_data.get("phone"):
        user_data["phone"] = phone_match.group(1)
        print(f"📞 Phone saved for {user_id}: {user_data['phone']}")
        bot.send_message(user_id, "📱 Got your contact number!")

    if email_match and not user_data.get("email"):
        user_data["email"] = email_match.group(0)
        print(f"📧 Email saved for {user_id}: {user_data['email']}")
        bot.send_message(user_id, "📧 Thanks! I’ve saved your email.")

    # 🏁 If both are available and lead not created yet → create in NetSuite
    if (
        user_data.get("phone")
        and user_data.get("email")
        and not user_data.get("lead_created")
    ):
        create_lead_in_netsuite(user_id, user_data, access_token)
        user_data["lead_created"] = True
        bot.send_message(user_id, "✅ You’ve been added as a lead in our system! Thank you 😊")

    # Continue normal AI chat
    ai_reply = ask_claude_with_mcp(conversation, access_token)
    conversation.append({"role": "assistant", "content": ai_reply})
    bot.send_message(user_id, ai_reply)

# ========================
# MAIN EXECUTION
# ========================
if __name__ == "__main__":
    print("🔐 Getting NetSuite authorization...")
    code = get_auth_code()
    tokens = exchange_code_for_token(code)
    print("✅ NetSuite access token obtained!")

    print("🚀 Bot is running... (Ctrl+C to stop)")
    bot.polling(non_stop=True)
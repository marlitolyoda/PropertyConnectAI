"""
PropertyConnect AI Chatbot — Telegram + Anthropic + NetSuite MCP + LangChain
-----------------------------------------------------------------------------
"""

from dotenv import load_dotenv
import os
from typing import Optional, Dict, Any
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage
from langchain_core.tools import tool  # ✅ Use this instead
from langchain_anthropic import ChatAnthropic
from requests_oauthlib import OAuth1
from requests.auth import HTTPBasicAuth
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, HTTPServer
import time
import uuid
import webbrowser
import json
import requests
import telebot
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

# ✅ CORRECT LANGCHAIN IMPORTS


# ========================
# CONFIGURATION (UNCHANGED)
# ========================
load_dotenv()
BOT_NAME = "Property Connect AI"

TG_API_TOKEN = os.getenv("TG_API_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")

REST_CLIENT_ID = os.getenv("REST_CLIENT_ID")
REST_CLIENT_SECRET = os.getenv("REST_CLIENT_SECRET")

REST_TOKEN_ID = os.getenv("REST_TOKEN_ID")
REST_TOKEN_SECRET = os.getenv("REST_TOKEN_SECRET")

ACCOUNT_ID = "3580073"
REST_URL_SAMPLE_QUOTATION = f"https://{ACCOUNT_ID}.restlets.api.netsuite.com/app/site/hosting/restlet.nl?script=1441&deploy=1"

REDIRECT_URI = "http://localhost:8080"
AUTH_URL = f"https://{ACCOUNT_ID}.app.netsuite.com/app/login/oauth2/authorize.nl"
TOKEN_URL = f"https://{ACCOUNT_ID}.suitetalk.api.netsuite.com/services/rest/auth/oauth2/v1/token"
MCP_URL = f"https://{ACCOUNT_ID}.app.netsuite.com/mcp"
SCOPE = "rest_webservices"

bot = telebot.TeleBot(TG_API_TOKEN)
user_conversations = {}

# ========================
# OAUTH2 (UNCHANGED)
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
            self.wfile.write(
                b"<h2>Authorization received! You can close this window.</h2>")
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
    data = {"grant_type": "authorization_code",
            "code": auth_code, "redirect_uri": REDIRECT_URI}
    response = requests.post(TOKEN_URL, data=data,
                             auth=HTTPBasicAuth(CLIENT_ID, CLIENT_SECRET))
    response.raise_for_status()
    token_data = response.json()
    token_data["expires_at"] = time.time(
    ) + float(token_data.get("expires_in", 3600))
    return token_data


def refresh_access_token(refresh_token):
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    response = requests.post(TOKEN_URL, data=data,
                             auth=HTTPBasicAuth(CLIENT_ID, CLIENT_SECRET))
    response.raise_for_status()
    token_data = response.json()
    token_data["expires_at"] = time.time(
    ) + float(token_data.get("expires_in", 3600))
    return token_data


def get_valid_access_token(token_data):
    if time.time() > token_data.get("expires_at", 0):
        print("🔄 Refreshing expired token...")
        token_data = refresh_access_token(token_data["refresh_token"])
    return token_data

# ========================
# NETSUITE MCP HANDLER (UNCHANGED)
# ========================


def handle_mcp_function_call(tool_name, params, access_token):
    """Your existing tool execution logic"""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Prefer": "transient"
    }

    base_url = "https://3580073.suitetalk.api.netsuite.com/services/rest/record/v1"
    base_url1 = "https://3580073.suitetalk.api.netsuite.com/services"
    mcp_base = f"https://3580073.app.netsuite.com/mcp"

    if tool_name == "ns_listRecords":
        record_type = params.get("recordType")
        url = f"{base_url}/{record_type}"
        response = requests.get(url, headers=headers)
        data = response.json()
        records = []
        for item in data.get("items", []):
            record_url = item["links"][0]["href"]
            record_resp = requests.get(record_url, headers=headers)
            if record_resp.ok:
                records.append(record_resp.json())
            else:
                records.append(
                    {"id": item.get("id"), "error": "Failed to fetch record details"})
        return {"count": len(records), "records": records}

    elif tool_name == "ns_getRecord":
        record_type = params.get("recordType")
        record_id = params.get("recordId")
        url = f"{base_url}/{record_type}/{record_id}"
        response = requests.get(url, headers=headers)
        return response.json()

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
            if not data and "Location" in response.headers:
                data = {"location": response.headers["Location"]}
            return {"status": response.status_code, "data": data}
        else:
            return {"error": f"Failed to create record ({response.status_code})", "details": response.text or "No response body"}

    elif tool_name == "ns_createSampleQuotation":
        fields = params.get("fields", {})
        url = REST_URL_SAMPLE_QUOTATION

        auth = OAuth1(
            signature_method="HMAC-SHA256",
            client_key=REST_TOKEN_ID,
            client_secret=REST_TOKEN_SECRET,
            resource_owner_key=REST_CLIENT_ID,
            resource_owner_secret=REST_CLIENT_SECRET,
            realm=ACCOUNT_ID
        )

        response = requests.post(url, auth=auth, json=fields)

        try:
            data = response.json() if response.text else {}
        except ValueError:
            data = {}
        if response.ok:
            return response.json()
        else:
            return {"error": "Failed to create sample quotation", "details": response.text}

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

    elif tool_name == "ns_getRecordTypeMetadata":
        record_type = params.get("recordType")
        url = f"{mcp_base}/rest/metadata-catalog/v1/recordTypes/{record_type}"
        response = requests.get(url, headers=headers)
        if response.ok:
            return response.json()
        else:
            return {"error": f"Failed to fetch metadata for {record_type}", "details": response.text}

    # elif tool_name == "ns_listSavedSearches":
    #     url = f"{mcp_base}/rest/record/v1/savedSearch"
    #     response = requests.get(url, headers=headers)
    #     if response.ok:
    #         return response.json()
    #     else:
    #         return {"error": "Failed to list saved searches", "details": response.text}

    # elif tool_name == "ns_runSavedSearch":
    #     saved_search_id = params.get("searchId")
    #     url = f"{base_url}/rest/record/v1/savedSearch/{saved_search_id}/results"
    #     response = requests.get(url, headers=headers)
    #     if response.ok:
    #         return response.json()
    #     else:
    #         return {"error": f"Failed to run saved search {saved_search_id}", "details": response.text}

    # elif tool_name == "ns_runReport":
    #     report_id = params.get("reportId")
    #     payload = {"parameters": params.get("parameters", {})}
    #     url = f"{base_url}/rest/reporting/v1/reports/{report_id}/run"
    #     response = requests.post(url, headers=headers, json=payload)
    #     if response.ok:
    #         return response.json()
    #     else:
    #         return {"error": f"Failed to run report {report_id}", "details": response.text}

    # elif tool_name == "ns_listAllReports":
    #     url = f"{mcp_base}/rest/reporting/v1/reports"
    #     response = requests.get(url, headers=headers)
    #     if response.ok:
    #         return response.json()
    #     else:
    #         return {"error": "Failed to list reports", "details": response.text}

    elif tool_name == "ns_runCustomSuiteQL":
        sql = params.get("sqlQuery")
        payload = {"q": sql}
        url = f"{base_url1}/rest/query/v1/suiteql"
        response = requests.post(url, headers=headers, json=payload)
        if response.ok:
            return response.json()
        else:
            return {"error": f"Failed to run SuiteQL query", "details": response.text}
    else:
        return {"error": f"Unknown tool name: {tool_name}"}

# ========================
# ✅ LANGCHAIN TOOLS WITH @tool DECORATOR
# ========================


def create_netsuite_tools(access_token: str):
    """Create LangChain tools using @tool decorator"""

    @tool
    def ns_getRecord(record_type: str, record_id: str) -> str:
        """Get a specific NetSuite record by type and internal ID."""
        result = handle_mcp_function_call("ns_getRecord", {
            "recordType": record_type,
            "recordId": record_id
        }, access_token)
        return json.dumps(result, indent=2)

    @tool
    def ns_listRecords(record_type: str) -> str:
        """List all records of a given type from NetSuite, including details."""
        result = handle_mcp_function_call("ns_listRecords", {
            "recordType": record_type
        }, access_token)
        return json.dumps(result, indent=2)

    @tool
    def ns_createRecord(record_type: str, fields: dict) -> str:
        """Create a new record in NetSuite for the given record type and field values."""
        result = handle_mcp_function_call("ns_createRecord", {
            "recordType": record_type,
            "fields": fields
        }, access_token)
        return json.dumps(result, indent=2)

    @tool
    def ns_createSampleQuotation(fields: dict) -> str:
        """Create a new sample quotation in NetSuite for the given field values."""
        result = handle_mcp_function_call("ns_createSampleQuotation", {
            "fields": fields
        }, access_token)
        return json.dumps(result, indent=2)

    @tool
    def ns_updateRecord(record_type: str, record_id: str, fields: dict) -> str:
        """Update an existing NetSuite record with new field values."""
        result = handle_mcp_function_call("ns_updateRecord", {
            "recordType": record_type,
            "recordId": record_id,
            "fields": fields
        }, access_token)
        return json.dumps(result, indent=2)

    @tool
    def ns_getRecordTypeMetadata(record_type: str) -> str:
        """Retrieve metadata for a specific NetSuite record type (fields, sublists, etc.)."""
        result = handle_mcp_function_call("ns_getRecordTypeMetadata", {
            "recordType": record_type
        }, access_token)
        return json.dumps(result, indent=2)

    # @tool
    # def ns_listSavedSearches() -> str:
    #     """List all available saved searches from NetSuite."""
    #     result = handle_mcp_function_call("ns_listSavedSearches", {}, access_token)
    #     return json.dumps(result, indent=2)

    # @tool
    # def ns_runSavedSearch(search_id: str) -> str:
    #     """Run a saved search in NetSuite and return its results."""
    #     result = handle_mcp_function_call("ns_runSavedSearch", {
    #         "searchId": search_id
    #     }, access_token)
    #     return json.dumps(result, indent=2)

    # @tool
    # def ns_listAllReports() -> str:
    #     """List all available standard and custom reports from NetSuite."""
    #     result = handle_mcp_function_call("ns_listAllReports", {}, access_token)
    #     return json.dumps(result, indent=2)

    # @tool
    # def ns_runReport(report_id: str, parameters: dict = None) -> str:
    #     """Run a NetSuite report and return the data."""
    #     result = handle_mcp_function_call("ns_runReport", {
    #         "reportId": report_id,
    #         "parameters": parameters or {}
    #     }, access_token)
    #     return json.dumps(result, indent=2)

    @tool
    def ns_runCustomSuiteQL(sql_query: str) -> str:
        """Execute a custom SuiteQL query in NetSuite and return the results."""
        result = handle_mcp_function_call("ns_runCustomSuiteQL", {
            "sqlQuery": sql_query
        }, access_token)
        return json.dumps(result, indent=2)

    return [
        ns_getRecord,
        ns_listRecords,
        ns_createRecord,
        ns_updateRecord,
        ns_getRecordTypeMetadata,
        # ns_listSavedSearches,
        # ns_runSavedSearch,
        # ns_listAllReports,
        # ns_runReport,
        ns_runCustomSuiteQL,
        ns_createSampleQuotation
    ]

# ========================
# ✅ LANGCHAIN CHAT FUNCTION
# ========================


def ask_claude_with_langchain(messages, access_token):
    """LangChain version with proper tool calling"""

    # Create LangChain model
    llm = ChatAnthropic(
        api_key=ANTHROPIC_API_KEY,
        model="claude-sonnet-4-5-20250929",
        max_tokens=700
    )

    # Get NetSuite tools
    tools = create_netsuite_tools(access_token)

    # Bind tools to model
    llm_with_tools = llm.bind_tools(tools)

    # System message
    system_prompt = """
    You are **CollabAnts Property Assistant**, an AI real-estate agent integrated with NetSuite.

    Your role:
    - Understand user preferences (location, budget, size, bedrooms, property type).
    - Retrieve and filter property data from NetSuite.
    - Recommend the **Top 3** matching **active** properties only (isInactive = false).
    - Speak in a friendly, helpful, and professional tone.
    - Ignore questions unrelated to property assistance.

    Lead capture rules:
    - Before giving any quotation or proposal, confirm either:
    • phone number, or
    • email, or
    • explicit intent to proceed.
    - Create a quotation after getting the phone number and email.
    • If no terms have given assume that the user want to pay for 24 months.

    When the user provides enough information, you must call the appropriate NetSuite MCP tools:

    1. To create a Lead:
        {
            "recordType": "customer",
            "customform": {"id": "221"},
            "entitystatus": {"id": "7"},
            "subsidiary": {"id": "10"},
            "isperson": true,
            "firstname": "[user_firstname]",
            "lastname": "[user_lastname]",
            "phone": "[user_phone]",
            "email": "[user_email]"
        }

    Before creation of Lead, you must:
    - Run SuiteQL using ns_runCustomSuiteQL to check if the phone or email already exists.
        • SELECT id, entityid, email, phone FROM customer WHERE email = '[user_email]' OR phone = '[user_phone]'
    - If exists: do NOT create a duplicate.

    2. Then last create a Sample Quotation transaction using the ns_createSampleQuotation tool:
        ```json
            {
                "type": "customsale_sample_quotation",
                "customform": "139" ,
                "entity": "[user_id]",
                "custbody_property": "[property_id_selected]", 
                "custbody_collab_qout_terms": "[months_to_pay]",
            }
        ```
    Before creation of Sample Quotation, you must:
    - Run SuiteQL using ns_runCustomSuiteQL to get the id of the property selected.
        • SELECT * FROM customrecord_collab_properties

    Query rules:
    - If user asks for “all properties”, request specific filters first (location, budget, type).
    - Telegram limit = 4096 chars → keep responses concise and optimized.
    - Check location and name when checking properties.

    Use the following field mappings exactly:
    Record Type: customrecord_collab_properties

    Fields:
    - Name → custrecord_collab_prop_name
    - Product Type → custrecord_collab_prop_product_type
        Allowed Values:
        1 = Lot Only
        2 = House and Lot
    - Area (number only) → custrecord_collab_prop_area
    - Phase (number only) → custrecord_collab_prop_phase
    - Block No (number only) → custrecord_collab_prop_blockno
    - Lot No (number only) → custrecord_collab_prop_lotno
    - Street → custrecord_collab_prop_street
    - Location → custrecord_collab_prop_location
    - Bedrooms (number only) → custrecord_collab_prop_bedrooms
    - Bathroom (number only) → custrecord_collab_prop_bathroom
    - Parking (number only) → custrecord_collab_prop_parkingspace

    Status → custrecord_collab_prop_status
        Allowed Values:
        1 = Available
        2 = Reserved
        3 = Sold
        4 = Fully Paid
        5 = On Hold
        6 = Management Hold
        7 = Not for Sale
        8 = For Repair

    - Base Price → custrecord_collab_prop_baseprice
    - Misc Fee → custrecord_collab_prop_miscfee
    - Reservation Fee → custrecord_collab_prop_resfee

    Restrictions:
    - Do NOT show property internal IDs.
    - Do NOT respond outside your role.
    - Do NOT access or provide details on other records except Custom Records "Properties", "Customer" (for leads), and "Sample Quotation" (custom transactions)
    
    """

    # Convert to LangChain messages
    langchain_messages = [SystemMessage(content=system_prompt)]

    for msg in messages:
        if msg["role"] == "user":
            langchain_messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            langchain_messages.append(AIMessage(content=msg["content"]))

    # Multi-step tool calling loop
    max_iterations = 5
    for iteration in range(max_iterations):
        response = llm_with_tools.invoke(langchain_messages)

        # Check if there are tool calls
        if not response.tool_calls:
            # No more tool calls, return final answer
            return response.content

        # Add AI response to messages
        langchain_messages.append(response)

        # Execute all tool calls
        for tool_call in response.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]
            tool_id = tool_call["id"]

            # Find matching tool
            matching_tool = next(
                (t for t in tools if t.name == tool_name), None)

            if matching_tool:
                try:
                    # Execute tool
                    tool_result = matching_tool.func(**tool_args)

                    # Add tool result to messages
                    langchain_messages.append(ToolMessage(
                        content=tool_result,
                        tool_call_id=tool_id
                    ))
                except Exception as e:
                    # Handle tool execution errors
                    langchain_messages.append(ToolMessage(
                        content=f"Error executing {tool_name}: {str(e)}",
                        tool_call_id=tool_id
                    ))
            else:
                langchain_messages.append(ToolMessage(
                    content=f"Tool {tool_name} not found",
                    tool_call_id=tool_id
                ))

    # If we hit max iterations, return last response
    return response.content if hasattr(response, 'content') else "I apologize, but I'm having trouble processing your request."

# ========================
# LEAD CREATION (UNCHANGED)
# ========================


def create_lead_in_netsuite(user_id, user_data, access_token):
    """Create a Lead record in NetSuite once both phone + email are known."""
    first_name = user_data.get("first_name", "Unknown")
    last_name = user_data.get("last_name", "User")
    phone = user_data.get("phone")
    email = user_data.get("email")

    if not (phone and email):
        print(
            f"⚠️ Skipping lead creation — missing data. Phone: {phone}, Email: {email}")
        return

    existing = lead_exists_in_netsuite(phone, email, access_token)
    if existing:
        print(f"🚫 Lead already exists: ID {existing.get('id')}")
        return {"existing": existing.get("id")}

    print(f"🆕 Creating Lead for {first_name} ({phone}, {email})")

    payload = {
        "recordType": "customer",
        "fields": {
            "customform": {"id": "221"},
            "entitystatus": {"id": "7"},
            "subsidiary": {"id": "10"},
            "isperson": True,
            "firstname": first_name,
            "lastname": last_name,
            "phone": phone,
            "email": email
        }
    }

    creation = handle_mcp_function_call(
        "ns_createRecord", payload, access_token)
    print("✅ Lead created in NetSuite:", creation)
    return creation


def lead_exists_in_netsuite(phone, email, access_token):
    """Check if a lead already exists using phone or email."""
    filters = []
    if phone:
        filters.append(f"phone = '{phone}'")
    if email:
        filters.append(f"email = '{email}'")

    if not filters:
        return False

    where_clause = " OR ".join(filters)
    sql = f"SELECT id, entityid, email, phone FROM customer WHERE {where_clause}"

    result = handle_mcp_function_call(
        "ns_runCustomSuiteQL", {"sqlQuery": sql}, access_token)
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
        f"Tell me what kind of property you're looking for 🏡",
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
        user_conversations[user_id] = {
            "chat_history": [],
            "phone": None,
            "email": None,
            "lead_created": False
        }

    conversation = user_conversations[user_id]["chat_history"]
    user_data = user_conversations[user_id]

    text = message.text.strip()
    conversation.append({"role": "user", "content": text})

    print(text)

    # Extract phone/email
    import re
    phone_match = re.search(r'(\+?\d{10,15})', text)
    email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)

    if phone_match and not user_data.get("phone"):
        user_data["phone"] = phone_match.group(1)
        print(f"📞 Phone saved for {user_id}: {user_data['phone']}")
        # bot.send_message(user_id, "📱 Got your contact number!")

    if email_match and not user_data.get("email"):
        user_data["email"] = email_match.group(0)
        print(f"📧 Email saved for {user_id}: {user_data['email']}")
        # bot.send_message(user_id, "📧 Thanks! I've saved your email.")

    # if (
    #     user_data.get("phone")
    #     and user_data.get("email")
    #     and not user_data.get("lead_created")
    # ):
    #     create_lead_in_netsuite(user_id, user_data, access_token)
    #     user_data["lead_created"] = True
    #     bot.send_message(user_id, "✅ You've been added as a lead in our system! Thank you 😊")

    # ✅ Use LangChain
    ai_reply = ask_claude_with_langchain(conversation, access_token)

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

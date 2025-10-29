import requests
import webbrowser
import uuid
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from requests.auth import HTTPBasicAuth

# ================= CONFIG =================
CLIENT_ID = "8527639950b0865a1ba5635aa6f280d08702bc80328708fa83bda5b099ab80d1"
CLIENT_SECRET = "9b51386c62737ea48ea6d8d7d0b2aecb0990577f5f856ebdb8d9bd8c9a8ce871"
REDIRECT_URI = "http://localhost:8080"
AUTH_URL = "https://3580073.app.netsuite.com/app/login/oauth2/authorize.nl"
TOKEN_URL = "https://3580073.suitetalk.api.netsuite.com/services/rest/auth/oauth2/v1/token"
SCOPE = "rest_webservices"
# ==========================================

# Generate random state for security
STATE = str(uuid.uuid4())

# Step 1: Construct authorization URL
auth_request_url = (
    f"{AUTH_URL}?response_type=code"
    f"&client_id={CLIENT_ID}"
    f"&redirect_uri={REDIRECT_URI}"
    f"&scope={SCOPE}"
    f"&state={STATE}"
)

# Step 2: Start temporary HTTP server to capture authorization code
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

    # Initialize attributes to avoid AttributeError
    httpd.auth_code = None
    httpd.state_received = None

    print("Opening browser for NetSuite login...")
    webbrowser.open(auth_request_url)
    print("Waiting for authorization code...")

    # Keep handling requests until we get the code
    while httpd.auth_code is None:
        httpd.handle_request()

    if httpd.state_received != STATE:
        raise ValueError("State mismatch. Potential CSRF attack!")

    return httpd.auth_code


# Step 3: Exchange authorization code for access token
def exchange_code_for_token(auth_code):
    data = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": REDIRECT_URI
    }
    response = requests.post(
        TOKEN_URL,
        data=data,
        auth=HTTPBasicAuth(CLIENT_ID, CLIENT_SECRET)
    )
    if response.status_code == 200:
        token_data = response.json()
        # Convert expires_in to float
        expires_in = float(token_data.get("expires_in", 3600))
        token_data["expires_at"] = time.time() + expires_in
        return token_data
    else:
        raise Exception(f"Error fetching token: {response.status_code} {response.text}")

# Step 4: Refresh the access token when expired
def refresh_access_token(refresh_token):
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token
    }
    response = requests.post(
        TOKEN_URL,
        data=data,
        auth=HTTPBasicAuth(CLIENT_ID, CLIENT_SECRET)
    )
    if response.status_code == 200:
        token_data = response.json()
        expires_in = float(token_data.get("expires_in", 3600))
        token_data["expires_at"] = time.time() + expires_in
        return token_data
    else:
        raise Exception(f"Error refreshing token: {response.status_code} {response.text}")

# Step 5: Wrapper to get valid access token
def get_valid_access_token(token_data):
    if time.time() > token_data.get("expires_at", 0):
        print("Access token expired. Refreshing...")
        token_data = refresh_access_token(token_data["refresh_token"])
    return token_data

# ======== MAIN FLOW ========
if __name__ == "__main__":
    # Step A: Get auth code from browser
    code = get_auth_code()
    print("Authorization code:", code)

    # Step B: Exchange auth code for access token
    tokens = exchange_code_for_token(code)
    print("Access Token:", tokens["access_token"])
    print("Refresh Token:", tokens.get("refresh_token"))

    # Step C: Example API call
    access_token = get_valid_access_token(tokens)["access_token"]
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    example_url = "https://3580073.suitetalk.api.netsuite.com/services/rest/record/v1/customrecord_collab_properties"

    response = requests.get(example_url, headers=headers)
    print("API Response Status:", response.status_code)
    print("API Response Body:", response.json())

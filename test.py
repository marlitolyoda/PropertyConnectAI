import webbrowser
import uuid

state = str(uuid.uuid4())
auth_url = (
    "https://3580073.app.netsuite.com/app/login/oauth2/authorize.nl"
    "?response_type=code"
    "&client_id=8527639950b0865a1ba5635aa6f280d08702bc80328708fa83bda5b099ab80d1"
    "&redirect_uri=http://localhost:8080"
    "&scope=rest_webservices"
    f"&state={state}"
)

webbrowser.open(auth_url)
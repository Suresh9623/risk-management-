import requests
from flask import Flask, jsonify

app = Flask(__name__)

# ==================== DHAN CONFIG ====================
DHAN_CLIENT_ID = "YOUR_CLIENT_ID"          # ← इथे तुझा Client ID टाक
DHAN_CLIENT_SECRET = "YOUR_CLIENT_SECRET"  # ← इथे तुझा Client Secret टाक
DHAN_ACCESS_TOKEN = "YOUR_TOTP_TOKEN"      # ← 30-sec बदलणारा TOTP Token

# ==================== GET MARGIN FUNCTION ====================
def get_dhan_margins():
    url = "https://api.dhan.co/v2/user/margins"

    headers = {
        "accept": "application/json",
        "X-Dhan-Client-Id": DHAN_CLIENT_ID,
        "X-Dhan-Client-Secret": DHAN_CLIENT_SECRET,
        "X-Dhan-Token": DHAN_ACCESS_TOKEN
    }

    response = requests.get(url, headers=headers)

    # जर काही error असेल तर JSON मध्ये दाखवायचं
    try:
        return response.json()
    except:
        return {"error": "Invalid Response", "status_code": response.status_code}

# ==================== API ROUTE ====================
@app.route('/')
def home():
    return "Dhan API Flask Server Running!"

@app.route('/margins')
def margins():
    data = get_dhan_margins()
    return jsonify(data)

# ==================== START FLASK ====================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)

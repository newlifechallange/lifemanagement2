from flask import Flask, request, jsonify
from core import LifeOSCore
import requests
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
core = LifeOSCore()

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    
    # Fonnte payload extraction
    phone = data.get('sender')
    message = data.get('message')
    name = data.get('name', 'Unknown User')

    if not phone or not message:
        return jsonify({"status": "ignored", "reason": "no sender or message"}), 200

    print(f"Received message from {name} ({phone}): {message}")

    try:
        # Process logic
        result = core.process_message(message, phone, name)
        response_text = result.get('response_text', "Sorry, I couldn't process that.")

        # Send reply
        send_whatsapp(phone, response_text)
    except Exception as e:
        print(f"Error processing message: {e}")
        send_whatsapp(phone, "I encountered an error. Please try again.")

    return jsonify({"status": "ok"}), 200

def send_whatsapp(target, message):
    url = "https://api.fonnte.com/send"
    headers = {
        "Authorization": os.getenv("FONNTE_TOKEN")
    }
    payload = {
        "target": target,
        "message": message
    }
    try:
        response = requests.post(url, headers=headers, data=payload)
        print(f"Fonnte Response: {response.text}")
    except Exception as e:
        print(f"Failed to send WhatsApp: {e}")

if __name__ == '__main__':
    # No init_db needed with Supabase client (Schema is managed via Dashboard/SQL)
    app.run(port=5000, debug=True)
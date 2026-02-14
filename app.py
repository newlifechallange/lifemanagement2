from flask import Flask, request, jsonify
from core import LifeOSCore
from db_client import supabase
import requests
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
core = LifeOSCore()

@app.route('/check-gaps', methods=['GET', 'POST'])
def check_gaps():
    try:
        users = supabase.table('users').select("id, phone_number, name").execute().data
        results = []
        for user in users:
            uid = user['id']
            notification = core.check_gaps_and_notify(uid)
            if notification:
                send_whatsapp(user['phone_number'], notification)
                results.append(f"Notified {user['name']}")
        return jsonify({"status": "ok", "results": results}), 200
    except Exception as e:
        print(f"Cron Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/check-cron', methods=['GET', 'POST'])
def check_cron():
    try:
        users = supabase.table('users').select("id, phone_number, name").execute().data
        results = []
        for user in users:
            uid = user['id']
            # 1. Check Timers
            timer_notif = core.check_timers(uid)
            if timer_notif:
                send_whatsapp(user['phone_number'], timer_notif)
                results.append(f"Timer Notified {user['name']}")
            
            # 2. Check Scheduled Tasks
            sched_notif = core.check_schedules(uid)
            if sched_notif:
                send_whatsapp(user['phone_number'], sched_notif)
                results.append(f"Schedule Notified {user['name']}")
                
        return jsonify({"status": "ok", "results": results}), 200
    except Exception as e:
        print(f"Cron Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    
    # Fonnte payload extraction
    phone = data.get('sender')
    message = data.get('message')
    name = data.get('name', 'Unknown User')
    # Fonnte usually sends 'id' for the message. If not, fallback to None (which skips dedup)
    message_id = data.get('id') 

    if not phone or not message:
        return jsonify({"status": "ignored", "reason": "no sender or message"}), 200

    print(f"Received message from {name} ({phone}): {message} [ID: {message_id}]")

    try:
        # Process logic
        result = core.process_message(message, phone, name, message_id)
        print(f"DEBUG: process_message result: {result}")
        
        # Check for duplicate or cooldown
        if result.get("status") == "duplicate":
            print(f"Skipping duplicate message ID: {message_id}")
            return jsonify({"status": "duplicate"}), 200
        
        if result.get("status") == "cooldown":
            print(f"Skipping due to cooldown.")
            return jsonify({"status": "cooldown"}), 200

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

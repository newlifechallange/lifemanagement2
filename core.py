import os
import json
import datetime
import pytz
import google.generativeai as genai
from db_client import supabase
from dotenv import load_dotenv

load_dotenv()

WIB = pytz.timezone('Asia/Jakarta')

class LifeOSCore:
    def __init__(self):
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        self.model = genai.GenerativeModel('gemini-3-flash-preview')
        self.histories = {} # Dict to store history per user: {user_id: [messages]}

    def get_or_create_user(self, phone_number: str, name: str):
        # Try to find user
        response = supabase.table('users').select("*").eq('phone_number', phone_number).execute()
        if response.data:
            return response.data[0]
        
        # Create user if not exists
        new_user = {
            "phone_number": phone_number,
            "name": name,
            "timezone": "Asia/Jakarta"
        }
        response = supabase.table('users').insert(new_user).execute()
        return response.data[0]

    def get_context(self, user_id: int):
        # Fetch logs for TODAY (since midnight WIB)
        now_wib = datetime.datetime.now(WIB)
        start_of_day = now_wib.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Supabase expects ISO string for comparison
        log_res = supabase.table('timelogs').select("*").eq('user_id', user_id).gte('start_time', start_of_day.isoformat()).order('start_time', desc=True).execute()
        logs = log_res.data
        
        # Parse timestamps for context
        log_context = []
        for l in logs:
            # Handle string timestamps from JSON API
            start = datetime.datetime.fromisoformat(l['start_time'].replace('Z', '+00:00')).astimezone(WIB)
            end = datetime.datetime.fromisoformat(l['end_time'].replace('Z', '+00:00')).astimezone(WIB)
            log_context.append({
                "id": l['id'],
                "activity": l['activity'],
                "category": l.get('category'), # Add category for summary
                "tag": l.get('tag'),
                "start": start.strftime("%H:%M"),
                "end": end.strftime("%H:%M"),
                "minutes": l['duration_minutes']
            })

        # Fetch current attributes
        attr_res = supabase.table('attributes').select("*").eq('user_id', user_id).execute()
        attr_context = {a['key']: {"value": a['value'], "unit": a['unit'], "notes": a.get('notes')} for a in attr_res.data}

        # Fetch pending plans
        plan_res = supabase.table('future_plans').select("*").eq('user_id', user_id).eq('status', 'pending').execute()
        plan_context = []
        for p in plan_res.data:
             planned = datetime.datetime.fromisoformat(p['planned_start'].replace('Z', '+00:00')).astimezone(WIB)
             plan_context.append({
                 "id": p['id'],
                 "activity": p['activity'],
                 "when": planned.strftime("%Y-%m-%d %H:%M")
             })

        # Fetch Attribute History (Last 50 entries) for context
        hist_res = supabase.table('attribute_history').select("*").eq('user_id', user_id).order('recorded_at', desc=True).limit(50).execute()
        history_context = []
        for h in hist_res.data:
            rec = datetime.datetime.fromisoformat(h['recorded_at'].replace('Z', '+00:00')).astimezone(WIB)
            history_context.append({
                "key": h['key'],
                "value": h['value'],
                "unit": h['unit'],
                "date": rec.strftime("%Y-%m-%d"),
                "notes": h.get('notes')
            })

        return log_context, attr_context, plan_context, history_context

    def process_message(self, user_input, phone_number, user_name):
        try:
            user = self.get_or_create_user(phone_number, user_name)
            user_id = user['id']

            # Initialize history
            if user_id not in self.histories:
                self.histories[user_id] = []

            log_context, attr_context, plan_context, history_context = self.get_context(user_id)
            current_time = datetime.datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S")

            system_instruction = f"""
            You are LifeOS, a personal assistant. Timezone: Asia/Jakarta (WIB).
            Current Date/Time: {current_time}
            User: {user_name}

            Context:
            - Today's Activity Logs (Since Midnight): {json.dumps(log_context)}
            - Current Attributes: {json.dumps(attr_context)}
            - Attribute History (Past changes): {json.dumps(history_context)}
            - Upcoming Plans: {json.dumps(plan_context)}
            - Chat History (last 5 turns): {json.dumps(self.histories[user_id])}

            Rules:
            1. Activities: If user mentions past activity, ASK to confirm time, then LOG_TIME.
            2. Plans: If user mentions FUTURE activity (e.g., "I will gym at 5pm"), ASK to confirm, then PLAN_ACTIVITY.
            3. Completion: If user says they DID a planned activity, use LOG_TIME and explicitly mention the plan_id in data to mark it complete.
            4. State: If user mentions state (weight), verify then UPDATE_STATE.
            5. Gaps: Check for >30min gaps between last activity and new activity start.
            6. Corrections: Handle typos using history.
            7. Categorization: Classify into: Work, Chore, Romantic, Rest, Entertainment, Exercise. If none fit, CREATE a new appropriate category (do not use 'Others').
            8. Queries: The user may ask for summaries (e.g., "How long did I work?"). Calculate this YOURSELF from "Today's Activity Logs" context. Sum the 'minutes' for matching categories/activities. If no logs exist, say so.
            9. Deletion: If user asks to delete/remove something, use DELETE action. For attributes, provide "key". For logs or plans, provide "id" (found in Context).
            10. Tags: If user specifies a tag (e.g. "project:1"), extract it.
            11. Intervals: If user describes interleaved time (e.g., "3 hours work, 5 min break every 30m"), DO NOT log every single interval. Calculate TOTAL WORK duration and TOTAL REST duration. Log them as two separate, consecutive entries. Add a note "Aggregated from intervals".

            Output Format (JSON ONLY):
            {{
              "response_text": "Friendly message answering the user or confirming action",
              "actions": [
                {{
                  "type": "LOG_TIME" | "UPDATE_STATE" | "PLAN_ACTIVITY" | "DELETE" | "QUERY",
                  "data": {{
                    "activity": "string", 
                    "start_time": "ISO_TIMESTAMP", 
                    "end_time": "ISO_TIMESTAMP", 
                    "category": "Work" | "Chore" | "Romantic" | "Rest" | "Entertainment" | "Exercise" | "CustomString",
                    "tag": "string",
                    "key": "attribute_key",
                    "value": "string_or_num",
                    "unit": "string",
                    "notes": "string",
                    "type": "timelog|attribute|plan",
                    "id": "integer_id"
                  }}
                }}
              ]
            }}
            Only include relevant keys in "data". Use current date {datetime.datetime.now(WIB).strftime("%Y-%m-%d")} for timestamps.
            """

            response = self.model.generate_content([system_instruction, user_input])
            res_text = response.text.strip()
            if res_text.startswith("```json"):
                res_text = res_text[7:-3].strip()
            
            result = json.loads(res_text)
            
            # Execute Actions
            actions = result.get("actions", [])
            # Support legacy single-action format if model slips (optional but safe)
            if "action" in result and "data" in result and not actions:
                actions = [{"type": result["action"], "data": result["data"]}]

            for act in actions:
                action_type = act.get("type")
                data = act.get("data", {})
                
                if action_type == "LOG_TIME" and data:
                    self.execute_log_time(user_id, data)
                elif action_type == "UPDATE_STATE" and data:
                    self.execute_update_state(user_id, data)
                elif action_type == "PLAN_ACTIVITY" and data:
                    self.execute_plan_activity(user_id, data)
                elif action_type == "DELETE" and data:
                    self.execute_delete(user_id, data)

            # Update history
            self.histories[user_id].append({"role": "user", "content": user_input})
            self.histories[user_id].append({"role": "assistant", "content": result['response_text']})
            if len(self.histories[user_id]) > 10:
                self.histories[user_id] = self.histories[user_id][-10:]

            return result
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"response_text": f"Error: {str(e)}", "action": "NONE", "data": {}}

    def execute_plan_activity(self, user_id, data):
        try:
            new_plan = {
                "user_id": user_id,
                "activity": data['activity'],
                "planned_start": data['start_time'],
                "planned_end": data.get('end_time'),
                "status": 'pending'
            }
            supabase.table('future_plans').insert(new_plan).execute()
        except Exception as e:
            print(f"Failed to plan activity: {e}")

    def execute_log_time(self, user_id, data):
        try:
            start = datetime.datetime.fromisoformat(data['start_time'])
            end = datetime.datetime.fromisoformat(data['end_time'])
            duration = int((end - start).total_seconds() / 60)
            
            new_log = {
                "user_id": user_id,
                "activity": data['activity'],
                "start_time": data['start_time'],
                "end_time": data['end_time'],
                "duration_minutes": duration,
                "category": data.get('category'),
                "tag": data.get('tag'),
                "notes": data.get('notes')
            }
            supabase.table('timelogs').insert(new_log).execute()
            
            if 'id' in data and data.get('type') == 'plan':
                 supabase.table('future_plans').update({"status": "completed"}).eq('id', data['id']).execute()

        except Exception as e:
            print(f"Failed to log time: {e}")

    def execute_update_state(self, user_id, data):
        try:
            # Upsert logic manually since we are tracking keys per user
            # First check if exists
            existing = supabase.table('attributes').select("*").eq('user_id', user_id).eq('key', data['key']).execute()
            
            if existing.data:
                # Update
                attr_id = existing.data[0]['id']
                update_data = {
                    "value": str(data['value']),
                    "unit": data.get('unit'),
                    "notes": data.get('notes'),
                    "updated_at": datetime.datetime.now(WIB).isoformat()
                }
                supabase.table('attributes').update(update_data).eq('id', attr_id).execute()
            else:
                # Insert
                new_attr = {
                    "user_id": user_id,
                    "key": data['key'],
                    "value": str(data['value']),
                    "unit": data.get('unit'),
                    "notes": data.get('notes')
                }
                supabase.table('attributes').insert(new_attr).execute()
            
            # --- LOG HISTORY ---
            history_entry = {
                "user_id": user_id,
                "key": data['key'],
                "value": str(data['value']),
                "unit": data.get('unit'),
                "notes": data.get('notes'),
                "recorded_at": datetime.datetime.now(WIB).isoformat()
            }
            supabase.table('attribute_history').insert(history_entry).execute()
            
        except Exception as e:
            print(f"Failed to update state: {e}")

    def execute_delete(self, user_id, data):
        try:
            table = ""
            if data.get('type') == 'timelog':
                table = 'timelogs'
            elif data.get('type') == 'attribute':
                table = 'attributes'
                # Attributes are deleted by key in our logic, but here we might receive ID. 
                # If key is provided:
                if 'key' in data:
                    supabase.table(table).delete().eq('user_id', user_id).eq('key', data['key']).execute()
                    return
            elif data.get('type') == 'plan':
                table = 'future_plans'
            
            if table and 'id' in data:
                supabase.table(table).delete().eq('id', data['id']).eq('user_id', user_id).execute()

        except Exception as e:
            print(f"Failed to delete: {e}")

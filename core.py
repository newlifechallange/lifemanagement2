import os
import json
import datetime
import pytz
from openai import OpenAI
from db_client import supabase
from dotenv import load_dotenv

load_dotenv()

WIB = pytz.timezone('Asia/Jakarta')

class LifeOSCore:
    def __init__(self):
        # OpenRouter Configuration
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )
        self.model_name = "arcee-ai/trinity-large-preview:free"

    def get_or_create_user(self, phone_number: str, name: str):
        response = supabase.table('users').select("*").eq('phone_number', phone_number).execute()
        if response.data:
            return response.data[0]
        
        new_user = {
            "phone_number": phone_number,
            "name": name,
            "timezone": "Asia/Jakarta"
        }
        response = supabase.table('users').insert(new_user).execute()
        return response.data[0]

    def get_context(self, user_id: int):
        # Fetch last 30 logs for context (gives a broader view than just today)
        log_res = supabase.table('timelogs').select("*").eq('user_id', user_id).order('start_time', desc=True).limit(30).execute()
        
        # Format logs as a readable string list for the AI
        log_strings = []
        for l in log_res.data:
            start = datetime.datetime.fromisoformat(l['start_time'].replace('Z', '+00:00')).astimezone(WIB)
            cat = l.get('category') or "None"
            log_strings.append(f"- {start.strftime('%Y-%m-%d %H:%M')} | {l['activity']} | Category: {cat} | Duration: {l['duration_minutes']} min")
        
        log_ctx_text = "\n".join(log_strings) if log_strings else "No logs found."

        attr_res = supabase.table('attributes').select("*").eq('user_id', user_id).execute()
        attr_context = {a['key']: {"value": a['value'], "unit": a['unit'], "notes": a.get('notes')} for a in attr_res.data}

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

        chat_res = supabase.table('chat_history').select("*").eq('user_id', user_id).order('created_at', desc=True).limit(10).execute()
        chat_context = [{"role": c['role'], "content": c['content']} for c in reversed(chat_res.data)]

        return log_ctx_text, attr_context, history_context, chat_context

    def process_message(self, user_input, phone_number, user_name, message_id=None):
        try:
            user = self.get_or_create_user(phone_number, user_name)
            user_id = user['id']

            # --- DEDUPLICATION CHECK ---
            if message_id:
                # Check if this message was already processed
                existing = supabase.table('chat_history').select('id').eq('message_id', message_id).execute()
                if existing.data:
                    return {"response_text": "", "action": "NONE", "status": "duplicate"}

                # Log User Message IMMEDIATELY to "lock" it
                supabase.table('chat_history').insert({
                    "user_id": user_id, 
                    "role": "user", 
                    "content": user_input,
                    "message_id": message_id, 
                    "created_at": datetime.datetime.now(WIB).isoformat()
                }).execute()
            else:
                 # Fallback for testing without IDs
                 supabase.table('chat_history').insert({
                    "user_id": user_id, 
                    "role": "user", 
                    "content": user_input,
                    "created_at": datetime.datetime.now(WIB).isoformat()
                }).execute()

            log_ctx, attr_ctx, hist_ctx, chat_ctx = self.get_context(user_id)
            now_wib = datetime.datetime.now(WIB)
            today_str = now_wib.strftime("%Y-%m-%d")
            
            daily_briefing_prompt = ""
            last_active = user.get('last_active_date')
            
            if last_active != today_str:
                current_streak = user.get('current_streak', 0)
                yesterday_str = (now_wib - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
                new_streak = current_streak + 1 if last_active == yesterday_str else 1
                
                supabase.table('users').update({
                    "last_active_date": today_str,
                    "current_streak": new_streak
                }).eq('id', user_id).execute()

                yest_start = (now_wib - datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0).isoformat()
                yest_end = now_wib.replace(hour=0, minute=0, second=0).isoformat()
                yest_logs = supabase.table('timelogs').select("*").eq('user_id', user_id).gte('start_time', yest_start).lt('start_time', yest_end).execute().data
                
                achievements_res = supabase.table('achievements').select("*").eq('user_id', user_id).execute()
                existing_achievements = [{"name": a['name'], "tier": a['tier']} for a in achievements_res.data]

                daily_briefing_prompt = f"""
                [SPECIAL SYSTEM EVENT: FIRST MESSAGE OF THE DAY]
                - Streak: {new_streak} days!
                - Yesterday's Activities: {json.dumps([l['activity'] for l in yest_logs])}
                - Existing Achievements: {json.dumps(existing_achievements)}
                INSTRUCTION: Start your response with a "Start of Day Briefing" summarizing streak and yesterday.
                """

            system_instruction = f"""
            You are LifeOS. Time: {now_wib.strftime("%Y-%m-%d %H:%M")}
            User: {user_name}

            Context:
            - Recent Activity Logs:
            {log_ctx}
            
            - Attributes: {json.dumps(attr_ctx)}
            - Attribute History: {json.dumps(hist_ctx)}
            - Chat History: {json.dumps(chat_ctx)}

            {daily_briefing_prompt}

            Rules:
            1. LOG_TIME: Log past OR future activities. For future plans, just set the start_time to the future date.
            2. UPDATE_STATE: Update user metrics (weight, etc).
            3. DELETE: Remove logs or attributes.
            4. UNLOCK_ACHIEVEMENT: Award achievements based on impressive data.
            5. SUMMARY: When asked to list, show, or summarize the timeline/activities, you MUST use the 'Recent Activity Logs' provided above. Do not just say "Here is your timeline", actually LIST every relevant activity with its time and duration in the 'response_text' field. Categorize them as requested.

            Output Format (JSON ONLY):
            {{
              "response_text": "Detailed categorized list of activities here...",
              "actions": [
                {{
                  "type": "LOG_TIME" | "UPDATE_STATE" | "DELETE" | "QUERY" | "UNLOCK_ACHIEVEMENT",
                  "data": {{ ... }}
                }}
              ]
            }}
            """

            completion = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": user_input}
                ],
                temperature=0.7,
            )
            
            res_text = completion.choices[0].message.content.strip()
            if res_text.startswith("```json"): res_text = res_text[7:]
            if res_text.endswith("```"): res_text = res_text[:-3]
            result = json.loads(res_text.strip())
            print(f"DEBUG: AI Result: {json.dumps(result, indent=2)}")

            for act in result.get("actions", []):
                act_type = act.get("type")
                data = act.get("data", {})
                print(f"DEBUG: Executing action: {act_type} with data: {data}")
                
                if act_type == "LOG_TIME":
                    self.execute_log_time(user_id, data)
                elif act_type == "UPDATE_STATE":
                    self.execute_update_state(user_id, data)
                elif act_type == "DELETE":
                    self.execute_delete(user_id, data)
                elif action_type == "UNLOCK_ACHIEVEMENT":
                    self.execute_unlock_achievement(user_id, data)

            supabase.table('chat_history').insert({
                "user_id": user_id, "role": "assistant", "content": result['response_text'],
                "created_at": datetime.datetime.now(WIB).isoformat()
            }).execute()

            return result
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"response_text": f"Error: {str(e)}", "action": "NONE", "data": {}}

    def execute_log_time(self, user_id, data):
        try:
            print(f"DEBUG: execute_log_time called with data: {data}")
            
            # 1. Flexible Key Mapping
            start_str = data.get('start_time') or data.get('start')
            end_str = data.get('end_time')
            minutes = data.get('minutes')
            activity = data.get('activity')

            if not start_str:
                print("DEBUG: No start time provided.")
                return

            now = datetime.datetime.now(WIB)
            
            # 2. Parse Start Time
            # Handle "HH:MM" vs "ISO"
            if "T" in start_str:
                start_dt = datetime.datetime.fromisoformat(start_str)
            else:
                # Assume HH:MM for today
                h, m = map(int, start_str.split(':'))
                start_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
                
                # If start time is in the future relative to "now" (e.g. logging 23:00 at 01:00)
                # it likely means yesterday.
                if start_dt > now + datetime.timedelta(minutes=30): 
                    start_dt -= datetime.timedelta(days=1)

            if start_dt.tzinfo is None:
                start_dt = WIB.localize(start_dt)

            # 3. Handle End Time / Duration
            if end_str:
                if "T" in end_str:
                    end_dt = datetime.datetime.fromisoformat(end_str)
                else:
                    h, m = map(int, end_str.split(':'))
                    end_dt = start_dt.replace(hour=h, minute=m)
                    if end_dt < start_dt:
                        end_dt += datetime.timedelta(days=1)
            elif minutes:
                end_dt = start_dt + datetime.timedelta(minutes=int(minutes))
            else:
                print("DEBUG: No end_time or minutes provided.")
                return

            if end_dt.tzinfo is None:
                end_dt = WIB.localize(end_dt)

            duration = int((end_dt - start_dt).total_seconds() / 60)
            
            insert_data = {
                "user_id": user_id, 
                "activity": activity, 
                "start_time": start_dt.isoformat(),
                "end_time": end_dt.isoformat(), 
                "duration_minutes": duration, 
                "category": data.get('category'), 
                "tag": data.get('tag'), 
                "notes": data.get('notes')
            }
            
            print(f"DEBUG: Inserting into timelogs: {insert_data}")
            res = supabase.table('timelogs').insert(insert_data).execute()
            print(f"DEBUG: Insert success: {res.data[0]['id'] if res.data else 'Failed'}")
            
        except Exception as e: 
            print(f"Log Time Error: {e}")
            import traceback
            traceback.print_exc()

    def execute_update_state(self, user_id, data):
        try:
            existing = supabase.table('attributes').select("*").eq('user_id', user_id).eq('key', data['key']).execute()
            update_data = {
                "value": str(data['value']), "unit": data.get('unit'), "notes": data.get('notes'),
                "updated_at": datetime.datetime.now(WIB).isoformat()
            }
            if existing.data:
                supabase.table('attributes').update(update_data).eq('id', existing.data[0]['id']).execute()
            else:
                update_data.update({"user_id": user_id, "key": data['key']})
                supabase.table('attributes').insert(update_data).execute()
            
            history_entry = update_data.copy()
            history_entry["recorded_at"] = datetime.datetime.now(WIB).isoformat()
            if 'updated_at' in history_entry: del history_entry['updated_at']
            supabase.table('attribute_history').insert(history_entry).execute()
        except Exception as e: print(f"Update State Error: {e}")

    def execute_delete(self, user_id, data):
        try:
            table = ""
            if data.get('type') == 'timelog': table = 'timelogs'
            elif data.get('type') == 'attribute':
                if 'key' in data:
                    supabase.table('attributes').delete().eq('user_id', user_id).eq('key', data['key']).execute()
                    return
            
            if table and 'id' in data:
                supabase.table(table).delete().eq('id', data['id']).eq('user_id', user_id).execute()
        except Exception as e: print(f"Delete Error: {e}")

    def execute_unlock_achievement(self, user_id, data):
        try:
            name = data.get('name')
            if not name: return
            existing = supabase.table('achievements').select("*").eq('user_id', user_id).eq('name', name).execute()
            if existing.data:
                current_tier = existing.data[0]['tier']
                if current_tier < existing.data[0]['max_tier']:
                     supabase.table('achievements').update({
                         "tier": current_tier + 1, "last_updated_at": datetime.datetime.now(WIB).isoformat()
                     }).eq('id', existing.data[0]['id']).execute()
            else:
                supabase.table('achievements').insert({
                    "user_id": user_id, "name": name, "icon": data.get('icon', '🏆'),
                    "description": data.get('description'), "tier": 1
                }).execute()
        except Exception as e: print(f"Achievement Error: {e}")

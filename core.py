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
        # Fetch last 30 logs for context
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

            # --- EMERGENCY COOLDOWN CHECK ---
            last_msg = supabase.table('chat_history').select('created_at').eq('user_id', user_id).eq('role', 'assistant').order('created_at', desc=True).limit(1).execute()
            if last_msg.data:
                last_time = datetime.datetime.fromisoformat(last_msg.data[0]['created_at'].replace('Z', '+00:00')).astimezone(WIB)
                now_time = datetime.datetime.now(WIB)
                if (now_time - last_time).total_seconds() < 10:
                    return {"response_text": "", "action": "NONE", "status": "cooldown"}

            # --- ATOMIC DEDUPLICATION ---
            if message_id:
                try:
                    insert_res = supabase.table('chat_history').insert({
                        "user_id": user_id, "role": "user", "content": user_input,
                        "message_id": message_id, "created_at": datetime.datetime.now(WIB).isoformat()
                    }).execute()
                    if not insert_res.data: return {"response_text": "", "action": "NONE", "status": "duplicate"}
                except Exception as db_err:
                    if "duplicate key" in str(db_err).lower() or "unique" in str(db_err).lower():
                        return {"response_text": "", "action": "NONE", "status": "duplicate"}
                    raise db_err
            else:
                 supabase.table('chat_history').insert({
                    "user_id": user_id, "role": "user", "content": user_input,
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
                supabase.table('users').update({"last_active_date": today_str, "current_streak": new_streak}).eq('id', user_id).execute()
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
                INSTRUCTION: Start your response with a brief (max 2-3 sentences) "Start of Day Briefing". 
                Keep it in the SAME paragraph as your main response.
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

            RULES:
            1. 1-MESSAGE-RULE: Provide your entire response in ONE single bubble. Do not split.
            2. LOG_TIME: Log past OR future activities. For future plans, just set the start_time to the future date. IMPORTANT: If the user provides a list of activities, you MUST generate a 'LOG_TIME' action for EACH item. 
               - Extract Categories: Represented by '*' (e.g. *Work, *Exercise).
               - Extract Tags: Represented by '#' (e.g. #lifeapp, #urgent).
               - Use these to fill 'category' and 'tag' fields in 'data'.
            3. SCHEDULE_REMINDER: If user asks to be reminded of something (e.g., "Remind me to work at 9am").
               Data: {{"message": "string", "remind_at": "ISO_TIMESTAMP"}}
            4. UPDATE_STATE: Update user metrics (weight, goals, state). Extract key, value, unit, and notes.
            5. DELETE: Remove logs or attributes. Provide 'type' (timelog|attribute) and 'id' or 'key'.
            6. UNLOCK_ACHIEVEMENT: Award achievements based on impressive data.
            7. SUMMARY: When asked to list/summarize timeline, you MUST use 'Recent Activity Logs'. LIST every activity with time/duration. Categorize them. 

            Output Format (JSON ONLY):
            {{
              "response_text": "Detailed categorized list or greeting here...",
              "actions": [
                {{
                  "type": "LOG_TIME" | "UPDATE_STATE" | "DELETE" | "SCHEDULE_REMINDER" | "UNLOCK_ACHIEVEMENT",
                  "data": {{ ... }}
                }}
              ]
            }}
            """

            completion = self.client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "system", "content": system_instruction}, {"role": "user", "content": user_input}],
                temperature=0.7,
            )
            
            res_text = completion.choices[0].message.content.strip()
            if res_text.startswith("```json"): res_text = res_text[7:]
            if res_text.endswith("```"): res_text = res_text[:-3]
            result = json.loads(res_text.strip())

            for act in result.get("actions", []):
                act_type = act.get("type")
                data = act.get("data", {})
                if act_type == "LOG_TIME": self.execute_log_time(user_id, data)
                elif act_type == "UPDATE_STATE": self.execute_update_state(user_id, data)
                elif act_type == "DELETE": self.execute_delete(user_id, data)
                elif act_type == "UNLOCK_ACHIEVEMENT": self.execute_unlock_achievement(user_id, data)
                elif act_type == "SCHEDULE_REMINDER": self.execute_schedule_reminder(user_id, data)

            supabase.table('chat_history').insert({"user_id": user_id, "role": "assistant", "content": result['response_text'], "created_at": datetime.datetime.now(WIB).isoformat()}).execute()
            return result
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"response_text": f"Error: {str(e)}", "action": "NONE", "data": {}}

    def execute_schedule_reminder(self, user_id, data):
        try:
            remind_at = datetime.datetime.fromisoformat(data['remind_at'])
            if remind_at.tzinfo is None: remind_at = WIB.localize(remind_at)
            supabase.table('reminders').insert({
                "user_id": user_id,
                "message": data['message'],
                "remind_at": remind_at.isoformat(),
                "status": 'pending'
            }).execute()
        except Exception as e: print(f"Reminder Error: {e}")

    def execute_log_time(self, user_id, data):
        try:
            start_str = data.get('start_time') or data.get('start')
            end_str = data.get('end_time')
            minutes = data.get('minutes')
            if not start_str: return
            now = datetime.datetime.now(WIB)
            if "T" in start_str: start_dt = datetime.datetime.fromisoformat(start_str)
            else:
                h, m = map(int, start_str.split(':'))
                start_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if start_dt > now + datetime.timedelta(minutes=30): start_dt -= datetime.timedelta(days=1)
            if start_dt.tzinfo is None: start_dt = WIB.localize(start_dt)
            if end_str:
                if "T" in end_str: end_dt = datetime.datetime.fromisoformat(end_str)
                else:
                    h, m = map(int, end_str.split(':'))
                    end_dt = start_dt.replace(hour=h, minute=m)
                    if end_dt < start_dt: end_dt += datetime.timedelta(days=1)
            elif minutes: end_dt = start_dt + datetime.timedelta(minutes=int(minutes))
            else: return
            if end_dt.tzinfo is None: end_dt = WIB.localize(end_dt)
            duration = int((end_dt - start_dt).total_seconds() / 60)
            supabase.table('timelogs').insert({"user_id": user_id, "activity": data.get('activity'), "start_time": start_dt.isoformat(), "end_time": end_dt.isoformat(), "duration_minutes": duration, "category": data.get('category'), "tag": data.get('tag'), "notes": data.get('notes')}).execute()
        except Exception as e: print(f"Log Time Error: {e}")

    def execute_update_state(self, user_id, data):
        try:
            existing = supabase.table('attributes').select("*").eq('user_id', user_id).eq('key', data['key']).execute()
            update_data = {"value": str(data['value']), "unit": data.get('unit'), "notes": data.get('notes'), "updated_at": datetime.datetime.now(WIB).isoformat()}
            if existing.data: supabase.table('attributes').update(update_data).eq('id', existing.data[0]['id']).execute()
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
            if table and 'id' in data: supabase.table(table).delete().eq('id', data['id']).eq('user_id', user_id).execute()
        except Exception as e: print(f"Delete Error: {e}")

    def check_gaps_and_notify(self, user_id):
        try:
            now_wib = datetime.datetime.now(WIB)
            
            # --- 1. REMINDER CHECK ---
            reminders = supabase.table('reminders').select("*").eq('user_id', user_id).eq('status', 'pending').lte('remind_at', now_wib.isoformat()).execute().data
            if reminders:
                r = reminders[0]
                supabase.table('reminders').update({"status": "sent"}).eq('id', r['id']).execute()
                return f"🔔 **Reminder:** {r['message']}"

            # --- 2. GAP CHECK ---
            if now_wib.hour < 7: return None
            midnight = now_wib.replace(hour=0, minute=0, second=0, microsecond=0)
            logs = supabase.table('timelogs').select("*").eq('user_id', user_id).gte('start_time', midnight.isoformat()).order('start_time', desc=False).execute().data
            if not logs: return None
            first_log_start = datetime.datetime.fromisoformat(logs[0]['start_time'].replace('Z', '+00:00')).astimezone(WIB)
            current_pointer = first_log_start
            check_end_boundary = now_wib if now_wib.hour < 21 else now_wib.replace(hour=21, minute=0, second=0)
            gaps = []
            for log in logs:
                log_start = datetime.datetime.fromisoformat(log['start_time'].replace('Z', '+00:00')).astimezone(WIB)
                log_end = datetime.datetime.fromisoformat(log['end_time'].replace('Z', '+00:00')).astimezone(WIB)
                gap_minutes = (log_start - current_pointer).total_seconds() / 60
                if gap_minutes > 60: gaps.append({"start": current_pointer, "end": log_start, "minutes": int(gap_minutes)})
                if log_end > current_pointer: current_pointer = log_end
            final_gap_minutes = (check_end_boundary - current_pointer).total_seconds() / 60
            if final_gap_minutes > 60: gaps.append({"start": current_pointer, "end": check_end_boundary, "minutes": int(final_gap_minutes)})
            
            if not gaps: return None
            if len(gaps) == 1:
                g = gaps[0]
                return f"⚠️ **Gap Detected:** You have a {g['minutes']} min gap between {g['start'].strftime('%H:%M')} and {g['end'].strftime('%H:%M')}. What were you doing?"
            elif len(gaps) >= 2:
                g_to_fill = gaps[0]
                self.execute_log_time(user_id, {"activity": "Unproductive (Auto-filled)", "category": "Others", "start_time": g_to_fill['start'].isoformat(), "end_time": g_to_fill['end'].isoformat(), "notes": "Auto-filled by Chroniter."})
                return f"⚠️ **Multiple Gaps!** I auto-filled {g_to_fill['start'].strftime('%H:%M')}-{g_to_fill['end'].strftime('%H:%M')} as 'Unproductive'. Please fill the remaining gap ({gaps[1]['minutes']} min)!"
        except Exception as e:
            print(f"Cron Logic Error: {e}")
            return None

    def execute_unlock_achievement(self, user_id, data):
        try:
            name = data.get('name')
            if not name: return
            existing = supabase.table('achievements').select("*").eq('user_id', user_id).eq('name', name).execute()
            if existing.data:
                current_tier = existing.data[0]['tier']
                if current_tier < existing.data[0]['max_tier']:
                     supabase.table('achievements').update({"tier": current_tier + 1, "last_updated_at": datetime.datetime.now(WIB).isoformat()}).eq('id', existing.data[0]['id']).execute()
            else:
                supabase.table('achievements').insert({"user_id": user_id, "name": name, "icon": data.get('icon', '🏆'), "description": data.get('description'), "tier": 1}).execute()
        except Exception as e: print(f"Achievement Error: {e}")

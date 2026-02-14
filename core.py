# TIMEZONE POLICY:
# 1. DATABASE: Always store as UTC (Supabase standard).
# 2. PROCESSING: Convert to WIB (Asia/Jakarta) for all calculations and AI context.
# 3. DISPLAY: Always show to user in WIB.

import os
import json
import datetime
import pytz
import uuid
from openai import OpenAI
from db_client import supabase
from dotenv import load_dotenv

load_dotenv()

WIB = pytz.timezone('Asia/Jakarta')
UTC = pytz.UTC

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

    def get_context(self, user_id: int, user_input: str = ""):
        # Determine how many logs to fetch based on user query
        limit = 40
        now = datetime.datetime.now(WIB)
        
        # If user asks for summary, report, or a longer time period, fetch more
        query_lower = user_input.lower()
        if any(kw in query_lower for kw in ["summary", "summarize", "report", "history", "list", "all"]):
            limit = 150
            
        if "week" in query_lower:
            start_of_week = (now - datetime.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            log_res = supabase.table('timelogs').select("*").eq('user_id', user_id).gte('start_time', start_of_week.astimezone(UTC).isoformat()).order('start_time', desc=True).limit(200).execute()
        elif "month" in query_lower:
            start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            log_res = supabase.table('timelogs').select("*").eq('user_id', user_id).gte('start_time', start_of_month.astimezone(UTC).isoformat()).order('start_time', desc=True).limit(500).execute()
        else:
            log_res = supabase.table('timelogs').select("*").eq('user_id', user_id).order('start_time', desc=True).limit(limit).execute()
        
        # Format logs as a readable string list for the AI
        log_strings = []
        for l in log_res.data:
            start = datetime.datetime.fromisoformat(l['start_time'].replace('Z', '+00:00')).astimezone(WIB)
            end_str = l.get('end_time')
            if end_str:
                end = datetime.datetime.fromisoformat(end_str.replace('Z', '+00:00')).astimezone(WIB)
                time_range = f"{start.strftime('%H:%M')} - {end.strftime('%H:%M')}"
            else:
                time_range = f"{start.strftime('%H:%M')} (Started)"
                
            cat = l.get('category') or ""
            tag = l.get('tag') or ""
            cat_tag = f" [{cat} {tag}]".strip()
            if cat_tag == "[]" or not cat_tag: cat_tag = ""
            # Format: [ID] [HH:MM - HH:MM] Activity (Duration min) [Category #Tag]
            log_strings.append(f"- ID: {l['id']} | [{time_range}] {l['activity']} ({l['duration_minutes']} min){cat_tag}")
        
        log_ctx_text = "\n".join(reversed(log_strings)) if log_strings else "No logs found."

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

        # Fetch active stopwatches and timers
        stopwatch_res = supabase.table('stopwatches').select("*").eq('user_id', user_id).eq('status', 'running').execute()
        stopwatch_ctx = []
        for s in stopwatch_res.data:
            s_start = datetime.datetime.fromisoformat(s['started_at'].replace('Z', '+00:00')).astimezone(WIB)
            cat_tag = f" [{s.get('category') or ''} {s.get('tag') or ''}]".strip()
            if cat_tag == "[]": cat_tag = ""
            stopwatch_ctx.append(f"{s['label']}{cat_tag} (since {s_start.strftime('%H:%M')})")

        timer_res = supabase.table('timers').select("*").eq('user_id', user_id).eq('status', 'running').execute()
        timer_ctx = []
        for t in timer_res.data:
            t_start = datetime.datetime.fromisoformat(t['started_at'].replace('Z', '+00:00')).astimezone(WIB)
            cat_tag = f" [{t.get('category') or ''} {t.get('tag') or ''}]".strip()
            if cat_tag == "[]": cat_tag = ""
            timer_ctx.append(f"{t['label']}{cat_tag} ({t['duration_minutes']}m, since {t_start.strftime('%H:%M')})")

        # Fetch active scheduled tasks
        schedule_res = supabase.table('scheduled_tasks').select("*").eq('user_id', user_id).eq('status', 'active').execute()
        schedule_ctx = [f"Every {s['frequency_minutes']}m: {s['message']} (Active {s['start_hour_wib']}:00-{s['end_hour_wib']}:00 WIB)" for s in schedule_res.data]

        # Fetch unlogged stopwatches and timers (stopped but not in timeline)
        unlogged_sw = supabase.table('stopwatches').select("*").eq('user_id', user_id).eq('status', 'stopped').eq('is_logged', False).execute()
        unlogged_ctx = []
        for s in unlogged_sw.data:
            unlogged_ctx.append(f"STOPWATCH ID: {s['id']} | Label: {s['label']} (Not in timeline)")

        unlogged_t = supabase.table('timers').select("*").eq('user_id', user_id).eq('status', 'completed').eq('is_logged', False).execute()
        for t in unlogged_t.data:
            unlogged_ctx.append(f"TIMER ID: {t['id']} | Label: {t['label']} (Not in timeline)")

        chat_res = supabase.table('chat_history').select("*").eq('user_id', user_id).order('created_at', desc=True).limit(10).execute()
        chat_context = [{"role": c['role'], "content": c['content']} for c in reversed(chat_res.data)]

        return log_ctx_text, attr_context, history_context, chat_context, stopwatch_ctx, timer_ctx, schedule_ctx, unlogged_ctx

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
                    return {"response_text": "Please wait a moment before sending another message (cooldown).", "action": "NONE", "status": "cooldown"}

            # --- ATOMIC DEDUPLICATION ---
            if message_id:
                try:
                    insert_res = supabase.table('chat_history').insert({
                        "user_id": user_id, "role": "user", "content": user_input,
                        "message_id": message_id, "created_at": datetime.datetime.now(UTC).isoformat()
                    }).execute()
                    if not insert_res.data: return {"response_text": "", "action": "NONE", "status": "duplicate"}
                except Exception as db_err:
                    if "duplicate key" in str(db_err).lower() or "unique" in str(db_err).lower():
                        return {"response_text": "", "action": "NONE", "status": "duplicate"}
                    raise db_err
            else:
                 supabase.table('chat_history').insert({
                    "user_id": user_id, "role": "user", "content": user_input,
                    "created_at": datetime.datetime.now(UTC).isoformat()
                }).execute()

            log_ctx, attr_ctx, hist_ctx, chat_ctx, stopwatch_ctx, timer_ctx, schedule_ctx, unlogged_ctx = self.get_context(user_id, user_input)
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
            You are LifeOS, a rigid database command-line interface. 
            Time: {now_wib.strftime("%Y-%m-%d %H:%M")}
            User: {user_name}

            CONTEXT (DB STATE):
            - TIMELOGS: {log_ctx}
            - ACTIVE_STOPWATCHES: {json.dumps(stopwatch_ctx)}
            - ACTIVE_TIMERS: {json.dumps(timer_ctx)}
            - UNLOGGED_SESSIONS: {json.dumps(unlogged_ctx)}
            - SCHEDULES: {json.dumps(schedule_ctx)}
            - ATTRIBUTES: {json.dumps(attr_ctx)}

            COMMAND RULES:
            1. DATABASE-TONE: Your 'response_text' MUST be a raw data report or a direct question. No fluff.
            2. START_STOPWATCH: Use to begin tracking. Data: {{"label": "string", "category": "string", "tag": "string"}}
            3. STOP_STOPWATCH: Use to end tracking. Must match label from ACTIVE_STOPWATCHES. Data: {{"label": "string"}}
               - After stopping, ask: "Session stopped. Log to timeline? [Yes/No]"
            4. LOG_SESSION: Use ONLY when user confirms 'Yes' to log a session from UNLOGGED_SESSIONS.
               - Data: {{"type": "stopwatch"|"timer", "id": int}}
            5. EXTRACTION:
               - Labels are inside backticks: `label`
               - Categories start with *: *Category
               - Tags start with #: #tag

            Output Format (JSON ONLY):
            {{
              "response_text": "RAW_REPORT_OR_QUESTION",
              "actions": [{{ "type": "ACTION_TYPE", "data": {{...}} }}]
            }}
            """

            completion = self.client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "system", "content": system_instruction}, {"role": "user", "content": user_input}],
                temperature=0.1,
            )
            
            res_text = completion.choices[0].message.content.strip()
            if "{" in res_text and "}" in res_text:
                first_brace = res_text.find("{")
                last_brace = res_text.rfind("}")
                potential_json = res_text[first_brace:last_brace+1]
                try:
                    result = json.loads(potential_json)
                except:
                    try:
                        result = json.loads(res_text[res_text.rfind("{"):res_text.rfind("}")+1])
                    except:
                        result = {"response_text": res_text, "actions": []}
            else:
                result = {"response_text": res_text, "actions": []}

            failed_actions = []
            receipts = []
            for act in result.get("actions", []):
                act_type = act.get("type")
                data = act.get("data", {})
                try:
                    success = False
                    if act_type == "LOG_TIME":
                        success = self.execute_log_time(user_id, data)
                        if success: receipts.append(f"LOGGED: {data.get('activity')} ({data.get('duration') or data.get('minutes')} min)")
                    elif act_type == "UPDATE_STATE":
                        self.execute_update_state(user_id, data)
                        receipts.append(f"UPDATED: {data.get('key')} = {data.get('value')} {data.get('unit', '')}")
                        success = True
                    elif act_type == "DELETE":
                        success = self.execute_delete(user_id, data)
                        if success: receipts.append(f"DELETED: {data.get('type')} {data.get('id') or data.get('ids') or data.get('key')}")
                    elif act_type == "UNLOCK_ACHIEVEMENT":
                        self.execute_unlock_achievement(user_id, data)
                        receipts.append(f"ACHIEVEMENT: {data.get('name')}")
                        success = True
                    elif act_type == "SCHEDULE_REMINDER":
                        self.execute_schedule_reminder(user_id, data)
                        receipts.append(f"REMINDER SET: {data.get('message')}")
                        success = True
                    elif act_type == "CREATE_SCHEDULE":
                        self.execute_create_schedule(user_id, data)
                        receipts.append(f"SCHEDULED: {data.get('message')} every {data.get('frequency_minutes')} min")
                        success = True
                    elif act_type == "START_STOPWATCH":
                        success = self.execute_start_stopwatch(user_id, data)
                        if success: receipts.append(f"INSERT stopwatch | Label: {data.get('label')}")
                    elif act_type == "STOP_STOPWATCH":
                        success, actual_label = self.execute_stop_stopwatch(user_id, data)
                        if success: receipts.append(f"UPDATE stopwatch | Status: stopped | Label: {actual_label}")
                    elif act_type == "START_TIMER":
                        self.execute_start_timer(user_id, data)
                        receipts.append(f"INSERT timer sequence | Steps: {len(data.get('timers', []))}")
                        success = True
                    elif act_type == "LOG_SESSION":
                        success = self.execute_log_session(user_id, data)
                        if success: receipts.append(f"INSERT timelog | Source: {data.get('type')} ID {data.get('id')}")
                    
                    if not success:
                        failed_actions.append(f"{act_type}: {data.get('activity') or data.get('label') or data.get('message') or data.get('id') or 'Unknown'}")
                except Exception as e:
                    err_msg = str(e)
                    if "column" in err_msg.lower():
                        err_msg = "Database schema mismatch. Please run the provided SQL updates in Supabase."
                    failed_actions.append(f"{act_type} ({err_msg})")

            # Finalize response_text with extreme rigidity
            ai_text = result.get('response_text', "").strip()
            
            if receipts:
                final_response = "✅ **DATABASE CONFIRMATION:**\n" + "\n".join([f"- {r}" for r in receipts])
                # If there was a question being answered alongside the action, keep the answer
                # But if the AI text looks like its own action summary, discard it
                if ai_text and not any(kw in ai_text.lower() for kw in ["action:", "logged", "started", "stopwatch", "timer"]):
                    final_response = f"{final_response}\n\n{ai_text}"
            elif ai_text:
                final_response = ai_text
            else:
                final_response = "No database changes recorded."

            if failed_actions:
                failure_block = "\n\n⚠️ **FAILED TO SAVE:**\n" + "\n".join([f"- {f}" for f in failed_actions])
                final_response += failure_block

            if final_response:
                supabase.table('chat_history').insert({"user_id": user_id, "role": "assistant", "content": final_response, "created_at": datetime.datetime.now(UTC).isoformat()}).execute()
            
            result['response_text'] = final_response
            return result
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"response_text": f"Error: {str(e)}", "action": "NONE", "data": {}}

    def execute_create_schedule(self, user_id, data):
        freq = int(data.get('frequency_minutes', 60))
        # Set next_run_at to now + freq
        next_run = datetime.datetime.now(UTC) + datetime.timedelta(minutes=freq)
        supabase.table('scheduled_tasks').insert({
            "user_id": user_id,
            "message": data['message'],
            "frequency_minutes": freq,
            "start_hour_wib": data.get('start_hour_wib', 0),
            "end_hour_wib": data.get('end_hour_wib', 23),
            "next_run_at": next_run.isoformat(),
            "status": 'active'
        }).execute()

    def check_schedules(self, user_id):
        try:
            now_utc = datetime.datetime.now(UTC)
            now_wib = datetime.datetime.now(WIB)
            
            res = supabase.table('scheduled_tasks').select("*").eq('user_id', user_id).eq('status', 'active').lte('next_run_at', now_utc.isoformat()).execute()
            
            notifications = []
            for task in res.data:
                # Check active window in WIB
                current_hour = now_wib.hour
                start_h = task.get('start_hour_wib', 0)
                end_h = task.get('end_hour_wib', 23)
                
                # Handle window wrap (e.g. 06:00 to 21:00)
                is_active = False
                if start_h <= end_h:
                    is_active = start_h <= current_hour < end_h
                else: # Wrap around midnight
                    is_active = current_hour >= start_h or current_hour < end_h
                
                if is_active:
                    notifications.append(f"📅 **Scheduled Task:** {task['message']}")
                
                # Update next_run_at regardless of whether we notified (to skip window)
                new_next_run = now_utc + datetime.timedelta(minutes=task['frequency_minutes'])
                supabase.table('scheduled_tasks').update({"next_run_at": new_next_run.isoformat()}).eq('id', task['id']).execute()
                
            return "\n".join(notifications) if notifications else None
        except Exception as e:
            print(f"Schedule Check Error: {e}")
            return None

    def execute_start_stopwatch(self, user_id, data):
        label = data.get('label', 'Unspecified')
        category = data.get('category')
        tag = data.get('tag')
        
        # Check if already running
        existing = supabase.table('stopwatches').select("*").eq('user_id', user_id).eq('label', label).eq('status', 'running').execute()
        if existing.data:
            return True # Already running is considered success
            
        res = supabase.table('stopwatches').insert({
            "user_id": user_id, 
            "label": label, 
            "category": category,
            "tag": tag,
            "status": 'running', 
            "started_at": datetime.datetime.now(UTC).isoformat()
        }).execute()
        return True if res.data else False

    def execute_stop_stopwatch(self, user_id, data):
        label = data.get('label')
        
        # 1. Try exact match
        query = supabase.table('stopwatches').select("*").eq('user_id', user_id).eq('status', 'running')
        if label:
            res = query.eq('label', label).order('started_at', desc=True).limit(1).execute()
            if not res.data:
                # 2. Try partial match
                all_running = supabase.table('stopwatches').select("*").eq('user_id', user_id).eq('status', 'running').execute()
                best_match = None
                for sw in all_running.data:
                    if label.lower() in sw['label'].lower() or sw['label'].lower() in label.lower():
                        best_match = sw
                        break
                if best_match: res.data = [best_match]
                else: res = supabase.table('stopwatches').select("*").eq('user_id', user_id).eq('status', 'running').order('started_at', desc=True).limit(1).execute()
        else:
            res = query.order('started_at', desc=True).limit(1).execute()
        
        if res.data:
            sw = res.data[0]
            # Just stop it. Don't log to timeline yet.
            supabase.table('stopwatches').update({
                "status": 'stopped',
                "ended_at": datetime.datetime.now(UTC).isoformat(),
                "is_logged": False
            }).eq('id', sw['id']).execute()
            return True, sw['label']
        return False, None

    def execute_log_session(self, user_id, data):
        try:
            stype = data.get('type')
            sid = data.get('id')
            if stype == 'stopwatch':
                res = supabase.table('stopwatches').select("*").eq('id', sid).eq('user_id', user_id).execute()
                if res.data:
                    sw = res.data[0]
                    start = datetime.datetime.fromisoformat(sw['started_at'].replace('Z', '+00:00')).astimezone(WIB)
                    end = datetime.datetime.fromisoformat(sw['ended_at'].replace('Z', '+00:00')).astimezone(WIB)
                    duration = int((end - start).total_seconds() / 60)
                    if duration <= 0: duration = 1
                    
                    log_data = {
                        "activity": sw['label'],
                        "start_time": start.isoformat(),
                        "end_time": end.isoformat(),
                        "duration": duration,
                        "category": sw.get('category'),
                        "tag": sw.get('tag')
                    }
                    if self.execute_log_time(user_id, log_data):
                        supabase.table('stopwatches').update({"is_logged": True}).eq('id', sid).execute()
                        return True
            elif stype == 'timer':
                res = supabase.table('timers').select("*").eq('id', sid).eq('user_id', user_id).execute()
                if res.data:
                    t = res.data[0]
                    start = datetime.datetime.fromisoformat(t['started_at'].replace('Z', '+00:00')).astimezone(WIB)
                    # Timer end time was stored or is now
                    end_time_str = t.get('ended_at')
                    if end_time_str:
                        end = datetime.datetime.fromisoformat(end_time_str.replace('Z', '+00:00')).astimezone(WIB)
                    else:
                        end = start + datetime.timedelta(minutes=t['duration_minutes'])
                    
                    log_data = {
                        "activity": f"Timer: {t['label']}",
                        "start_time": start.isoformat(),
                        "end_time": end.isoformat(),
                        "duration": t['duration_minutes'],
                        "category": t.get('category') or "Timer",
                        "tag": t.get('tag')
                    }
                    if self.execute_log_time(user_id, log_data):
                        supabase.table('timers').update({"is_logged": True}).eq('id', sid).execute()
                        return True
            return False
        except Exception as e:
            print(f"Log Session Error: {e}")
            return False

    def execute_start_timer(self, user_id, data):
        timers = data.get('timers', [])
        if not timers: return
        
        group_id = str(uuid.uuid4())
        now_utc = datetime.datetime.now(UTC)
        
        for i, t_data in enumerate(timers):
            status = 'running' if i == 0 else 'pending'
            started_at = now_utc.isoformat() if i == 0 else None
            supabase.table('timers').insert({
                "user_id": user_id,
                "label": t_data['label'],
                "category": t_data.get('category'),
                "tag": t_data.get('tag'),
                "duration_minutes": t_data['duration'],
                "status": status,
                "started_at": started_at,
                "sequence_group_id": group_id,
                "sequence_order": i
            }).execute()

    def check_timers(self, user_id):
        try:
            now_wib = datetime.datetime.now(WIB)
            now_utc = datetime.datetime.now(UTC)
            res = supabase.table('timers').select("*").eq('user_id', user_id).eq('status', 'running').execute()
            
            notifications = []
            for timer in res.data:
                started_at_wib = datetime.datetime.fromisoformat(timer['started_at'].replace('Z', '+00:00')).astimezone(WIB)
                duration = datetime.timedelta(minutes=timer['duration_minutes'])
                
                if now_wib >= started_at_wib + duration:
                    supabase.table('timers').update({
                        "status": 'completed',
                        "ended_at": now_utc.isoformat(),
                        "is_logged": False
                    }).eq('id', timer['id']).execute()
                    notifications.append(f"⏰ **Timer Finished:** {timer['label']} ({timer['duration_minutes']} min). Log to timeline? [Yes/No]")
                    
                    if timer['sequence_group_id']:
                        next_res = supabase.table('timers').select("*").eq('sequence_group_id', timer['sequence_group_id']).eq('sequence_order', timer['sequence_order'] + 1).execute()
                        if next_res.data:
                            next_timer = next_res.data[0]
                            supabase.table('timers').update({
                                "status": 'running',
                                "started_at": now_utc.isoformat()
                            }).eq('id', next_timer['id']).execute()
                            notifications.append(f"⏭️ **Next Timer Started:** {next_timer['label']} ({next_timer['duration_minutes']} min)")
            
            return "\n".join(notifications) if notifications else None
        except Exception as e:
            print(f"Timer Check Error: {e}")
            return None

    def execute_schedule_reminder(self, user_id, data):
        try:
            remind_str = data.get('remind_at')
            if not remind_str: return
            remind_at = datetime.datetime.fromisoformat(remind_str)
            if remind_at.tzinfo is None: remind_at = WIB.localize(remind_at)
            now = datetime.datetime.now(WIB)
            if remind_at < now - datetime.timedelta(hours=1):
                remind_at = remind_at.replace(year=now.year, month=now.month, day=now.day)
            supabase.table('reminders').insert({"user_id": user_id, "message": data['message'], "remind_at": remind_at.astimezone(UTC).isoformat(), "status": 'pending'}).execute()
        except Exception as e: print(f"Reminder Error: {e}")

    def execute_log_time(self, user_id, data):
        try:
            start_str = data.get('start_time') or data.get('start')
            end_str = data.get('end_time') or data.get('end')
            minutes = data.get('minutes') or data.get('duration')
            activity = data.get('activity') or "Unspecified Activity"
            if not start_str: return False
            now = datetime.datetime.now(WIB)
            if "T" in str(start_str): start_dt = datetime.datetime.fromisoformat(str(start_str))
            else:
                h, m = map(int, str(start_str).split(':'))
                start_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if start_dt > now + datetime.timedelta(minutes=30): start_dt -= datetime.timedelta(days=1)
            if start_dt.tzinfo is None: start_dt = WIB.localize(start_dt)
            if end_str:
                if "T" in str(end_str): end_dt = datetime.datetime.fromisoformat(str(end_str))
                else:
                    h, m = map(int, str(end_str).split(':'))
                    end_dt = start_dt.replace(hour=h, minute=m)
                    if end_dt < start_dt: end_dt += datetime.timedelta(days=1)
            elif minutes:
                clean_mins = "".join(filter(str.isdigit, str(minutes)))
                if not clean_mins: return False
                mins_val = int(clean_mins)
                if "hour" in str(minutes).lower() and mins_val < 24: mins_val *= 60
                end_dt = start_dt + datetime.timedelta(minutes=mins_val)
            else: return False
            if end_dt.tzinfo is None: end_dt = WIB.localize(end_dt)
            
            # Final duration in minutes
            if minutes:
                try:
                    clean_mins = "".join(filter(str.isdigit, str(minutes)))
                    duration = int(clean_mins)
                except:
                    duration = int((end_dt - start_dt).total_seconds() / 60)
            else:
                duration = int((end_dt - start_dt).total_seconds() / 60)
            
            # Absolute minimum 1 minute
            if duration <= 0:
                duration = 1

            res = supabase.table('timelogs').insert({"user_id": user_id, "activity": activity, "start_time": start_dt.astimezone(UTC).isoformat(), "end_time": end_dt.astimezone(UTC).isoformat(), "duration_minutes": duration, "category": data.get('category'), "tag": data.get('tag'), "notes": data.get('notes')}).execute()
            if not res.data:
                print(f"Log Error: Insert returned no data. Check Supabase RLS policies or triggers.")
                return False
            return True
        except Exception as e: 
            print(f"Log Error: {e}")
            return False

    def execute_update_state(self, user_id, data):
        try:
            existing = supabase.table('attributes').select("*").eq('user_id', user_id).eq('key', data['key']).execute()
            update_data = {"value": str(data['value']), "unit": data.get('unit'), "notes": data.get('notes'), "updated_at": datetime.datetime.now(UTC).isoformat()}
            if existing.data: supabase.table('attributes').update(update_data).eq('id', existing.data[0]['id']).execute()
            else:
                update_data.update({"user_id": user_id, "key": data['key']})
                supabase.table('attributes').insert(update_data).execute()
            history_entry = update_data.copy()
            history_entry["recorded_at"] = datetime.datetime.now(UTC).isoformat()
            if 'updated_at' in history_entry: del history_entry['updated_at']
            supabase.table('attribute_history').insert(history_entry).execute()
        except Exception as e: print(f"Update State Error: {e}")

    def execute_delete(self, user_id, data):
        try:
            table = ""
            dtype = data.get('type')
            if dtype == 'timelog': table = 'timelogs'
            elif dtype == 'scheduled_task': table = 'scheduled_tasks'
            elif dtype == 'attribute':
                if 'key' in data:
                    supabase.table('attributes').delete().eq('user_id', user_id).eq('key', data['key']).execute()
                    return True
            
            if table:
                # Handle single ID or list of IDs
                ids = data.get('id') or data.get('ids')
                if ids:
                    if isinstance(ids, list):
                        res = supabase.table(table).delete().in_('id', ids).eq('user_id', user_id).execute()
                    else:
                        res = supabase.table(table).delete().eq('id', ids).eq('user_id', user_id).execute()
                    return True if res.data else False
            return False
        except Exception as e: 
            print(f"Delete Error: {e}")
            return False

    def check_gaps_and_notify(self, user_id):
        try:
            now_wib = datetime.datetime.now(WIB)
            reminders = supabase.table('reminders').select("*").eq('user_id', user_id).eq('status', 'pending').lte('remind_at', now_wib.astimezone(UTC).isoformat()).execute().data
            if reminders:
                r = reminders[0]
                supabase.table('reminders').update({"status": "sent"}).eq('id', r['id']).execute()
                return f"🔔 **Reminder:** {r['message']}"
            
            if now_wib.hour < 7: return None
            
            midnight = now_wib.replace(hour=0, minute=0, second=0, microsecond=0)
            logs = supabase.table('timelogs').select("*").eq('user_id', user_id).gte('start_time', midnight.astimezone(UTC).isoformat()).order('start_time', desc=False).execute().data
            
            check_end_boundary = now_wib if now_wib.hour < 21 else now_wib.replace(hour=21, minute=0, second=0)
            
            if not logs:
                start_boundary = now_wib.replace(hour=7, minute=0, second=0, microsecond=0)
                if now_wib > start_boundary + datetime.timedelta(minutes=120):
                    gap_minutes = int((now_wib - start_boundary).total_seconds() / 60)
                    return f"⚠️ **Gap Detected:** You haven't logged anything today! You have a {gap_minutes} min gap since 07:00. What have you been doing?"
                return None

            first_log_start = datetime.datetime.fromisoformat(logs[0]['start_time'].replace('Z', '+00:00')).astimezone(WIB)
            current_pointer = first_log_start
            morning_start = now_wib.replace(hour=7, minute=0, second=0, microsecond=0)
            
            gaps = []
            if first_log_start > morning_start + datetime.timedelta(minutes=60):
                gaps.append({"start": morning_start, "end": first_log_start, "minutes": int((first_log_start - morning_start).total_seconds() / 60)})

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
                self.execute_log_time(user_id, {"activity": "Unproductive (Auto-filled)", "category": "Others", "start_time": g_to_fill['start'].astimezone(UTC).isoformat(), "end_time": g_to_fill['end'].astimezone(UTC).isoformat(), "notes": "Auto-filled by Chroniter."})
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
                     supabase.table('achievements').update({"tier": current_tier + 1, "last_updated_at": datetime.datetime.now(UTC).isoformat()}).eq('id', existing.data[0]['id']).execute()
            else:
                supabase.table('achievements').insert({"user_id": user_id, "name": name, "icon": data.get('icon', '🏆'), "description": data.get('description'), "tier": 1, "last_updated_at": datetime.datetime.now(UTC).isoformat()}).execute()
        except Exception as e: print(f"Achievement Error: {e}")

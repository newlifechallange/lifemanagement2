import os
import json
import datetime
import pytz
from openai import OpenAI
from db_client import supabase
from dotenv import load_dotenv

load_dotenv()

class POSCore:
    def __init__(self):
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )
        self.model_name = "arcee-ai/trinity-large-preview:free"

    # --- UTILS ---
    def get_locale(self, phone):
        p = str(phone).replace('+', '').strip()
        if p.startswith('62'):
            return pytz.timezone('Asia/Jakarta'), "Bahasa Indonesia"
        return pytz.timezone('UTC'), "English"

    def get_or_create_user(self, phone_number: str, name: str):
        response = supabase.table('users').select("*").eq('phone_number', phone_number).execute()
        if response.data:
            user = response.data[0]
        else:
            new_user = { "phone_number": phone_number, "name": name }
            response = supabase.table('users').insert(new_user).execute()
            user = response.data[0]
        return user

    def get_active_store(self, user_id, current_tz):
        user = supabase.table('users').select("*").eq('id', user_id).execute().data[0]
        
        # Daily Reset Check
        now = datetime.datetime.now(current_tz)
        today_str = now.strftime("%Y-%m-%d")
        last_active = user.get('last_active_at')
        
        print(f"[DEBUG] User: {user.get('name')} | ActiveID: {user.get('active_store_id')} | LastActive: {last_active} | Today: {today_str}")

        if not last_active:
             print("[DEBUG] No last_active, reset.")
             return None, None

        # Fix format if needed
        try:
            last_date = datetime.datetime.fromisoformat(last_active.replace('Z', '+00:00')).astimezone(current_tz).strftime("%Y-%m-%d")
            if last_date != today_str:
                print(f"[DEBUG] Date mismatch: {last_date} != {today_str}")
                return None, None # Force reset
        except Exception as e:
            print(f"[DEBUG] Date parse error: {e}")
            return None, None

        sid = user.get('active_store_id')
        if not sid: return None, None

        # Verify Access
        link = supabase.table('store_users').select("role, status").eq('store_id', sid).eq('user_id', user_id).eq('status', 'active').execute()
        if not link.data: return None, None
        
        store = supabase.table('stores').select("*").eq('id', sid).execute().data[0]
        return store, link.data[0]['role']

    def check_permission(self, role, action_type):
        permissions = {
            "admin": ["ALL"],
            "manager": ["ALL_EXCEPT_DELETE"],
            "cashier": ["CHECKOUT", "ADD_TO_CART", "REGISTER_CUSTOMER", "REPAY_DEBT", "GENERATE_RECEIPT", "MANAGE_SHIFT"],
            "stock_op": ["UPDATE_INVENTORY", "REGISTER_PRODUCT"]
        }
        allowed = permissions.get(role, [])
        if "ALL" in allowed or "ALL_EXCEPT_DELETE" in allowed: return True
        return action_type in allowed

    # --- ROUTER ---
    def route_intent(self, user_input, role):
        system_prompt = f"""
        You are a Router for a POS System. Classify the user intent.
        USER ROLE: {role}
        
        INTENTS:
        - SALES: Selling, Checkout, Add to Cart, Debt/Kasbon, "John owes for [item]", "John paid debt".
        - INVENTORY: Stock updates, Buying ingredients, "Beli beras untuk stok", "Set harga".
        - FINANCE: Expenses (Non-stock), Personal withdrawals, Shifts, "Bayar listrik", "Ambil uang".
        - REPORTING: "Laporan", "Omzet", "Profit", "Summary".
        - STORE: Switch store, Invite employee, Create store.
        - GENERAL: Greetings, "Hi", "Help".

        OUTPUT JSON ONLY: {{ "intent": "SALES" | "INVENTORY" | "FINANCE" | "REPORTING" | "STORE" | "GENERAL" }}
        """
        
        import time
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_input}]
                )
                break
            except Exception as e:
                if "429" in str(e) and attempt < max_retries - 1:
                    time.sleep(5 * (attempt + 1))
                    continue
                else:
                    print(f"Router LLM Error: {e}")
                    return "GENERAL" # Fallback

        try:
            txt = response.choices[0].message.content.strip()
            if "```json" in txt: txt = txt.split("```json")[1].split("```")[0].strip()
            return json.loads(txt).get("intent", "GENERAL")
        except:
            return "GENERAL"

    # --- EXPERT HANDLERS ---
    def handle_sales(self, user_input, user_id, store_id, role, lang, tz):
        # Context: Cart, Catalog, Customers
        cart_res = supabase.table('cart_items').select("*, products(name, price)").eq('store_id', store_id).eq('user_id', user_id).execute()
        cart = [ {"name": c['products']['name'], "qty": c['quantity'], "price": c['products']['price']} for c in cart_res.data if c['products'] ]
        
        # Smart Catalog Search (Vector-like simulation for MVP)
        # For now, just fetch top 50. In prod, search based on input keywords.
        catalog = supabase.table('products').select("id, name, price, stock").eq('store_id', store_id).limit(50).execute().data
        customers = supabase.table('customers').select("name, debt").eq('store_id', store_id).limit(10).execute().data

        prompt = f"""
        You are the SALES Expert. Role: {role}. Lang: {lang}.
        CONTEXT:
        - CART: {json.dumps(cart)}
        - CATALOG: {json.dumps(catalog)}
        - CUSTOMERS: {json.dumps(customers)}
        
        TASKS:
        1. **Add to Cart**: Match user input to Catalog.
        2. **Checkout**: 
           - **IMPORTANT**: If user says "Sell [Item] and checkout", you MUST provide TWO actions: 1. ADD_TO_CART, then 2. CHECKOUT.
           - **DEBT (Piutang)**: If user says "John owes for [Item]", you MUST use "John" as `customer_name`. Do NOT use "customer1" if a name is provided.
           - Methods: Cash, Bank, QRIS, Piutang (Debt).
        3. **Repayment**: "John paid 50k debt" -> you MUST use `REPAY_DEBT` action. (Use EXACT name, e.g. John).

        JSON OUTPUT: {{ "response_text": "...", "actions": [ {{ "type": "REPAY_DEBT", "data": {{ "customer_name": "John", "amount": 50000 }} }} ] }}
        
        MANDATORY: 
        - Output ONLY valid JSON.
        - **NO TRANSLATION**: Keep names EXACTLY as in Context/Input.
        """
        return self.run_llm(prompt, user_input, user_id, store_id, role, tz)

    def handle_inventory(self, user_input, user_id, store_id, role, lang, tz):
        catalog = supabase.table('products').select("id, name, price, cost_price, stock").eq('store_id', store_id).limit(50).execute().data
        
        prompt = f"""
        You are the INVENTORY Expert. Role: {role}. Lang: {lang}.
        CONTEXT:
        - CATALOG: {json.dumps(catalog)}
        
        TASKS:
        1. **Purchase (Stock In)**: "Bought 5kg Rice for 50k"
           - Calculates Unit Cost (50k/5 = 10k).
           - Updates Stock (+5).
           - Records Expense (Type="expense", Category="COGS").
           - Action: UPDATE_INVENTORY.
        2. **Usage (Stock Out)**: "Used 2 eggs" -> Stock -2.
        3. **Pricing**: "Set price of Kopi to 5k" -> UPDATE_PRODUCT.

        JSON OUTPUT: {{ "response_text": "...", "actions": [ {{ "type": "UPDATE_INVENTORY", "data": {{ "product_name": "Str", "stock_change": Int, "cost_price": Float }} }} ] }}
        
        MANDATORY: Output ONLY valid JSON. **NO TRANSLATION** of names. Always include 'product_name'.
        """
        return self.run_llm(prompt, user_input, user_id, store_id, role, tz)

    def handle_finance(self, user_input, user_id, store_id, role, lang, tz):
        # Context: Recent History, Shift Status
        history = supabase.table('transactions').select("transaction_type, net_amount, category, notes").eq('store_id', store_id).order('created_at', desc=True).limit(5).execute().data
        shift = supabase.table('shifts').select("*").eq('store_id', store_id).eq('user_id', user_id).eq('status', 'open').execute().data
        
        prompt = f"""
        You are the FINANCE Expert. Role: {role}. Lang: {lang}.
        CONTEXT:
        - HISTORY: {json.dumps(history)}
        - SHIFT: {json.dumps(shift)}
        
        TASKS:
        1. **Expenses**: "Paid electricity 100k". Record as Expense.
        2. **Personal**: "Took 50k for lunch". Record as Personal (net negative).
        3. **Shifts**: Start/Close shift.

        JSON OUTPUT: {{ "response_text": "...", "actions": [ {{ "type": "RECORD_TRANSACTION"|"MANAGE_SHIFT", "data": {{...}} }} ] }}
        
        MANDATORY: Output ONLY valid JSON.
        """
        return self.run_llm(prompt, user_input, user_id, store_id, role, tz)

    def handle_reporting(self, user_input, user_id, store_id, role, lang, tz):
        # Aggregated Analytics
        now = datetime.datetime.now(tz)
        today = now.replace(hour=0, minute=0, second=0).isoformat()
        month = now.replace(day=1, hour=0, minute=0).isoformat()
        
        def get_sums(date_filter):
            res = supabase.table('transactions').select("net_amount, transaction_type").eq('store_id', store_id).gte('created_at', date_filter).execute()
            rev = sum(float(t['net_amount']) for t in res.data if t['transaction_type'] == 'income')
            exp = sum(float(t['net_amount']) for t in res.data if t['transaction_type'] == 'expense')
            return {"revenue": rev, "expense": exp, "net": rev + exp}

        stats = { "today": get_sums(today), "month": get_sums(month) }
        
        prompt = f"""
        You are the ANALYST. Role: {role}. Lang: {lang}.
        DATA: {json.dumps(stats)}
        
        TASK: Summarize performance. Do not output ACTIONS, just text.
        JSON OUTPUT: {{ "response_text": "Report...", "actions": [] }}
        MANDATORY: Output ONLY valid JSON.
        """
        return self.run_llm(prompt, user_input, user_id, store_id, role, tz)

    def handle_store_mgmt(self, user_input, user_id, store_id, role, lang, tz):
        # Switch store, invites
        stores = supabase.table('store_users').select("stores(name)").eq('user_id', user_id).eq('status', 'active').execute().data
        
        prompt = f"""
        You are the STORE MANAGER.
        MY STORES: {json.dumps(stores)}
        TASKS: Switch store, Invite employees, Create store.
        JSON OUTPUT: {{ "response_text": "...", "actions": [ {{ "type": "SWITCH_STORE"|"CREATE_STORE"|"INVITE_EMPLOYEE"|"ACCEPT_INVITE", "data": {{...}} }} ] }}
        
        MANDATORY: Output ONLY valid JSON. **NO TRANSLATION** of store names (Keep input name exactly).
        """
        return self.run_llm(prompt, user_input, user_id, store_id, role, tz)

    def handle_general(self, user_input, user_id, store_id, role, lang, tz):
        # Fallback / Greetings
        prompt = f"""
        You are POS-SME. Helpful Assistant.
        Lang: {lang}.
        User: {role}.
        Explain what I can do (Sales, Inventory, Finance, Reports).
        JSON OUTPUT: {{ "response_text": "...", "actions": [] }}
        MANDATORY: Output ONLY valid JSON.
        """
        return self.run_llm(prompt, user_input, user_id, store_id, role, tz)

    def run_llm(self, system_prompt, user_input, user_id, store_id, role, current_tz):
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_input}]
        
        import time
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(model=self.model_name, messages=messages)
                break
            except Exception as e:
                if "429" in str(e) and attempt < max_retries - 1:
                    time.sleep(5 * (attempt + 1))
                    continue
                else:
                    print(f"LLM Error: {e}")
                    return {"response_text": "Error processing request due to AI limits.", "actions": []}
        
        try:
            txt = response.choices[0].message.content.strip()
            # Clean markdown
            if "```json" in txt: txt = txt.split("```json")[1].split("```")[0].strip()
            elif "```" in txt: txt = txt.split("```")[1].split("```")[0].strip()
            
            result = json.loads(txt)
            
            # Execute Actions
            for act in result.get("actions", []):
                # Global Permission Check
                if not self.check_permission(role, act['type']):
                    continue
                self.execute_action(user_id, store_id, act['type'], act.get('data', {}), current_tz)
            
            return result
        except Exception as e:
            print(f"LLM Error: {e}")
            return {"response_text": "Error processing request.", "actions": []}

    # --- MAIN PROCESS ---
    def process_message(self, user_input, phone_number, user_name, message_id=None):
        if user_input.strip() == "123reset123": return self.reset_database()
        
        user_tz, user_lang = self.get_locale(phone_number)
        user = self.get_or_create_user(phone_number, user_name)
        
        # 1. Store Context
        store, role = self.get_active_store(user['id'], user_tz)
        
        # 2. No Store Flow
        if not store:
            # Simple direct prompt for onboarding
            return self.handle_store_mgmt(user_input, user['id'], None, "admin", user_lang, user_tz)

        # 3. Router
        intent = self.route_intent(user_input, role)
        print(f"[DEBUG] Intent: {intent} | Input: {user_input}")

        # 4. Dispatch
        if intent == "SALES":
            return self.handle_sales(user_input, user['id'], store['id'], role, user_lang, user_tz)
        elif intent == "INVENTORY":
            return self.handle_inventory(user_input, user['id'], store['id'], role, user_lang, user_tz)
        elif intent == "FINANCE":
            return self.handle_finance(user_input, user['id'], store['id'], role, user_lang, user_tz)
        elif intent == "REPORTING":
            return self.handle_reporting(user_input, user['id'], store['id'], role, user_lang, user_tz)
        elif intent == "STORE":
            return self.handle_store_mgmt(user_input, user['id'], store['id'], role, user_lang, user_tz)
        else:
            return self.handle_general(user_input, user['id'], store['id'], role, user_lang, user_tz)

    # --- ACTIONS (Keep existing logic, just ensure completeness) ---
    def reset_database(self):
        tables = ['chat_history', 'transaction_items', 'cart_items', 'transactions', 'shifts', 'customers', 'product_bundles', 'products', 'store_users', 'stores', 'users']
        for t in tables:
            try: supabase.table(t).delete().neq('id', 0).execute()
            except: pass
        return {"response_text": "⚠️ SYSTEM RESET COMPLETE.", "actions": []}

    def execute_action(self, user_id, store_id, action_type, data, current_tz):
        try:
            now_iso = datetime.datetime.now(current_tz).isoformat()
            
            # Default update
            user_update = {'last_active_at': now_iso}

            if action_type == "CREATE_STORE":
                name = data.get('name') or data.get('store_name') or 'My Store'
                stype = data.get('type') or data.get('store_type') or 'retail'
                res = supabase.table('stores').insert({"name": name, "type": stype, "owner_id": user_id}).execute()
                if res.data:
                    sid = res.data[0]['id']
                    supabase.table('store_users').insert({"store_id": sid, "user_id": user_id, "role": "admin", "status": "active"}).execute()
                    user_update['active_store_id'] = sid
            
            # Apply user update once
            supabase.table('users').update(user_update).eq('id', user_id).execute()
            
            if not store_id and action_type != "CREATE_STORE": return

            if action_type == "UPDATE_INVENTORY" or action_type == "UPDATE_PRODUCT":
                name = data.get('product_name') or data.get('name') or data.get('product') or data.get('item_name')
                # Flatten fields
                for k in ['fields', 'changes', 'updates']:
                    if isinstance(data.get(k), dict): data.update(data[k])
                
                if name:
                    p_res = supabase.table('products').select("id, stock").eq('store_id', store_id).ilike('name', name).execute()
                    if p_res.data:
                        pid = p_res.data[0]['id']
                        updates = {k: v for k, v in data.items() if k in ['price', 'stock', 'status', 'cost_price', 'category']}
                        
                        # Math Rule: Unit Cost
                        if 'total_cost' in data and 'quantity' in data:
                            try: updates['cost_price'] = float(data['total_cost']) / float(data['quantity'])
                            except: pass

                        if 'stock_change' in data: 
                            updates['stock'] = int(p_res.data[0]['stock'] + float(data['stock_change']))
                        elif 'quantity' in data and 'cost_price' in data: # Purchase logic
                             updates['stock'] = int(p_res.data[0]['stock'] + float(data['quantity']))
                        
                        if 'price' in updates: updates['status'] = 'verified'
                        
                        supabase.table('products').update(updates).eq('id', pid).execute()
                        
                        # If Purchase, also Record Transaction
                        if 'cost_price' in data and 'quantity' in data:
                             total = float(data.get('total_cost', float(data['cost_price']) * float(data['quantity'])))
                             supabase.table('transactions').insert({
                                "store_id": store_id, "user_id": user_id, "transaction_type": "expense", 
                                "category": "COGS", "total_amount": total, "net_amount": -total, "created_at": now_iso
                             }).execute()

                    else:
                        # Create
                        stock = float(data.get('stock_change') or data.get('quantity') or 0)
                        supabase.table('products').insert({
                            "store_id": store_id, "name": name, "price": data.get('price', 0), 
                            "cost_price": data.get('cost_price', 0), "stock": int(stock), "status": "needs_review"
                        }).execute()

            elif action_type == "CHECKOUT":
                acc = (data.get('account') or data.get('payment_method') or 'cash').lower()
                c_name = data.get('customer_name') or data.get('customer') or 'customer1'
                cust = supabase.table('customers').select("id, debt").eq('store_id', store_id).ilike('name', c_name).execute()
                if cust.data:
                    cid, c_debt = (cust.data[0]['id'], float(cust.data[0]['debt']))
                else:
                    new_c = supabase.table('customers').insert({"store_id": store_id, "name": c_name}).execute()
                    cid, c_debt = (new_c.data[0]['id'], 0) if new_c.data else (None, 0)

                cart = supabase.table('cart_items').select("*, products(id, name, price, stock)").eq('store_id', store_id).eq('user_id', user_id).execute()
                if not cart.data: return
                total = sum(c['products']['price'] * c['quantity'] for c in cart.data)
                
                tid_res = supabase.table('transactions').insert({
                    "store_id": store_id, "user_id": user_id, "transaction_type": "income", "scope": "business", 
                    "category": "Sales", "account": acc, "customer_id": cid, "total_amount": total, 
                    "net_amount": 0 if acc=='piutang' else total, "created_at": now_iso
                }).execute()
                
                if tid_res.data:
                    tid = tid_res.data[0]['id']
                    if acc == 'piutang' and cid: supabase.table('customers').update({"debt": c_debt + total}).eq('id', cid).execute()
                    for c in cart.data:
                        pid, qty = c['product_id'], c['quantity']
                        supabase.table('transaction_items').insert({"transaction_id": tid, "product_id": pid, "product_name": c['products']['name'], "quantity": qty, "price_at_sale": c['products']['price']}).execute()
                        supabase.table('products').update({"stock": c['products']['stock'] - qty}).eq('id', pid).execute()
                    supabase.table('cart_items').delete().eq('store_id', store_id).eq('user_id', user_id).execute()

            elif action_type == "ADD_TO_CART":
                name = data.get('product_name') or data.get('name') or data.get('product') or data.get('item')
                pid = data.get('product_id') or data.get('id')
                
                if pid:
                    p_res = supabase.table('products').select("id").eq('store_id', store_id).eq('id', pid).execute()
                elif name:
                    p_res = supabase.table('products').select("id").eq('store_id', store_id).ilike('name', name).execute()
                else:
                    p_res = None

                if p_res and p_res.data:
                    target_pid = p_res.data[0]['id']
                    qty = data.get('quantity') or data.get('qty', 1)
                    exist = supabase.table('cart_items').select("*").eq('store_id', store_id).eq('user_id', user_id).eq('product_id', target_pid).execute()
                    if exist.data: supabase.table('cart_items').update({"quantity": exist.data[0]['quantity'] + qty}).eq('id', exist.data[0]['id']).execute()
                    else: supabase.table('cart_items').insert({"store_id": store_id, "user_id": user_id, "product_id": target_pid, "quantity": qty}).execute()

        except Exception as e: print(f"Action Error: {e}")

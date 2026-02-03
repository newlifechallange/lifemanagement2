from core import LifeOSCore
from db_client import supabase
import datetime

def test_history():
    core = LifeOSCore()
    
    # 1. Setup: Use the dummy user from main_window.py or create a test one
    phone = "0000000000"
    name = "Test User"
    user = core.get_or_create_user(phone, name)
    user_id = user['id']
    print(f"Testing with User ID: {user_id}")

    # 2. Define test data
    test_key = "test_metric"
    val1 = "100"
    val2 = "150"
    
    print("\n--- Step 1: First Update ---")
    data1 = {"key": test_key, "value": val1, "unit": "points"}
    core.execute_update_state(user_id, data1)
    print(f"Updated {test_key} to {val1}")

    print("\n--- Step 2: Second Update ---")
    data2 = {"key": test_key, "value": val2, "unit": "points"}
    core.execute_update_state(user_id, data2)
    print(f"Updated {test_key} to {val2}")

    # 3. Verify
    print("\n--- Verification ---")
    
    # Check Current State
    curr = supabase.table('attributes').select("*").eq('user_id', user_id).eq('key', test_key).execute()
    if curr.data:
        print(f"Current Value in DB: {curr.data[0]['value']} (Expected: {val2})")
    else:
        print("ERROR: Attribute not found in main table.")

    # Check History
    hist = supabase.table('attribute_history').select("*").eq('user_id', user_id).eq('key', test_key).order('recorded_at', desc=True).execute()
    print(f"History entries found: {len(hist.data)}")
    
    for h in hist.data:
        print(f" - [{h['recorded_at']}] {h['key']}: {h['value']}")

    if len(hist.data) >= 2:
        print("\nSUCCESS: History logging works.")
    else:
        print("\nFAILURE: History not logged correctly (might need to create the table first!).")

if __name__ == "__main__":
    test_history()

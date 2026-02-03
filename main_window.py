import sys
import datetime
import pytz
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QTextEdit, QLineEdit, QPushButton, QLabel)
from PyQt6.QtCore import Qt, pyqtSlot
from core import LifeOSCore
from db_client import supabase

WIB = pytz.timezone('Asia/Jakarta')

class LifeOSApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.core = LifeOSCore()
        self.initUI()
        self.update_status_panel()

    def initUI(self):
        self.setWindowTitle('LifeOS Desktop MVP (Supabase Client)')
        self.setGeometry(100, 100, 900, 700) 

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        # Left: Chat Area
        chat_container = QWidget()
        chat_layout = QVBoxLayout(chat_container)
        
        self.chat_area = QTextEdit()
        self.chat_area.setReadOnly(True)
        chat_layout.addWidget(self.chat_area)

        input_layout = QHBoxLayout()
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Say something... (e.g., 'I weigh 70kg' or 'I just worked out for 1 hour')")
        self.input_field.returnPressed.connect(self.send_message)
        self.send_button = QPushButton('Send')
        self.send_button.clicked.connect(self.send_message)
        
        input_layout.addWidget(self.input_field)
        input_layout.addWidget(self.send_button)
        chat_layout.addLayout(input_layout)
        
        main_layout.addWidget(chat_container, 2)

        # Right: Status Panel
        self.status_panel = QTextEdit()
        self.status_panel.setReadOnly(True)
        self.status_panel.setMaximumWidth(300)
        main_layout.addWidget(self.status_panel, 1)

        # Initial message
        self.append_chat("LifeOS", "Hello! I'm your LifeOS assistant. How's your day going?")

    def append_chat(self, sender, message):
        self.chat_area.append(f"<b>{sender}:</b> {message}<br>")

    def update_status_panel(self):
        try:
            # MVP: Dummy Phone
            user_res = supabase.table('users').select("*").eq('phone_number', "0000000000").execute()
            if not user_res.data:
                self.status_panel.setHtml("<h2>Current State</h2><i>Waiting for first message...</i>")
                return

            user_id = user_res.data[0]['id']

            # Fetch Attributes
            attrs = supabase.table('attributes').select("*").eq('user_id', user_id).execute().data
            # Fetch last 5 logs
            logs = supabase.table('timelogs').select("*").eq('user_id', user_id).order('end_time', desc=True).limit(5).execute().data
            # Fetch pending plans
            plans = supabase.table('future_plans').select("*").eq('user_id', user_id).eq('status', 'pending').order('planned_start', desc=False).execute().data
            
            status_text = "<h2>Current State</h2>"
            status_text += "<b>Attributes:</b><br>"
            if not attrs:
                status_text += "<i>No attributes recorded.</i><br>"
            for a in attrs:
                status_text += f"• {a['key']}: {a['value']} {a['unit'] or ''}<br>"
            
            status_text += "<br><b>Upcoming Plans:</b><br>"
            if not plans:
                status_text += "<i>No upcoming plans.</i><br>"
            for p in plans:
                planned = datetime.datetime.fromisoformat(p['planned_start'].replace('Z', '+00:00')).astimezone(WIB)
                status_text += f"• {planned.strftime('%d/%m %H:%M')}: {p['activity']}<br>"

            status_text += "<br><b>Recent Timeline:</b><br>"
            if not logs:
                status_text += "<i>No activity logged.</i><br>"
            for l in logs:
                start = datetime.datetime.fromisoformat(l['start_time'].replace('Z', '+00:00')).astimezone(WIB)
                end = datetime.datetime.fromisoformat(l['end_time'].replace('Z', '+00:00')).astimezone(WIB)
                status_text += f"• {start.strftime('%H:%M')} - {end.strftime('%H:%M')}: {l['activity']}<br>"
            
            self.status_panel.setHtml(status_text)
        except Exception as e:
            self.status_panel.setHtml(f"<i>Error fetching status: {e}</i>")

    @pyqtSlot()
    def send_message(self):
        user_text = self.input_field.text().strip()
        if not user_text:
            return

        self.input_field.clear()
        self.append_chat("You", user_text)

        # Process through Core
        # MVP: Using dummy phone/name for desktop user
        result = self.core.process_message(user_text, "0000000000", "Desktop User")
        
        # Display AI Response
        self.append_chat("LifeOS", result.get("response_text", "I'm having trouble processing that."))
        
        # Update Status
        self.update_status_panel()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = LifeOSApp()
    ex.show()
    sys.exit(app.exec())
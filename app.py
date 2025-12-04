import os
import sqlite3
import json
import base64
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()

app = Flask(__name__)

# Configuration
DB_NAME = "letters.db"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS letters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_date TEXT NOT NULL,
            send_date TEXT,
            sender_name TEXT,
            sender_address TEXT,
            recipient_name TEXT,
            recipient_address TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/scan', methods=['POST'])
def scan_letter():
    if not GEMINI_API_KEY:
        return jsonify({"error": "Gemini API Key not configured. Please check .env file."}), 500

    try:
        data = request.json
        image_data = data.get('image') # Base64 string

        if not image_data:
            return jsonify({"error": "No image provided"}), 400

        # Remove header if present (e.g., "data:image/jpeg;base64,")
        if ',' in image_data:
            image_data = image_data.split(',')[1]

        image_bytes = base64.b64decode(image_data)

        # Call Gemini API
        model = genai.GenerativeModel('gemini-2.0-flash')
        
        prompt = """
        Analyze this image of a letter/envelope. Extract the following information in JSON format:
        - sender_name: The name of the sender.
        - sender_address: The address of the sender.
        - recipient_name: The name of the recipient.
        - recipient_address: The address of the recipient.
        - send_date: The date the letter was sent (if visible on postmark or letter). Format as YYYY-MM-DD if possible, else null.
        
        If a field is not found, use null.
        Return ONLY the JSON.
        """

        response = model.generate_content([
            {'mime_type': 'image/jpeg', 'data': image_bytes},
            prompt
        ])

        # Parse response
        try:
            text_response = response.text
            # Clean up potential markdown code blocks
            if text_response.startswith('```json'):
                text_response = text_response[7:]
            if text_response.endswith('```'):
                text_response = text_response[:-3]
            
            extracted_data = json.loads(text_response)
        except Exception as e:
            return jsonify({"error": f"Failed to parse AI response: {str(e)}", "raw_response": response.text}), 500

        # Save to DB
        scan_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO letters (scan_date, send_date, sender_name, sender_address, recipient_name, recipient_address)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            scan_date,
            extracted_data.get('send_date'),
            extracted_data.get('sender_name'),
            extracted_data.get('sender_address'),
            extracted_data.get('recipient_name'),
            extracted_data.get('recipient_address')
        ))
        conn.commit()
        new_id = cur.lastrowid
        conn.close()

        extracted_data['id'] = new_id
        extracted_data['scan_date'] = scan_date
        
        # Add usage metadata if available
        if hasattr(response, 'usage_metadata'):
            extracted_data['usage'] = {
                'prompt_token_count': response.usage_metadata.prompt_token_count,
                'candidates_token_count': response.usage_metadata.candidates_token_count,
                'total_token_count': response.usage_metadata.total_token_count
            }

        return jsonify(extracted_data)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/history', methods=['GET'])
def get_history():
    conn = get_db_connection()
    letters = conn.execute('SELECT * FROM letters ORDER BY scan_date DESC').fetchall()
    conn.close()
    
    return jsonify([dict(ix) for ix in letters])

@app.route('/api/status', methods=['GET'])
def check_status():
    status = {
        "gemini_connected": False,
        "model": "gemini-2.0-flash"
    }
    
    if GEMINI_API_KEY:
        try:
            # Simple list models call to verify key
            genai.list_models()
            status["gemini_connected"] = True
        except Exception:
            status["gemini_connected"] = False
            
    return jsonify(status)

if __name__ == '__main__':
    app.run(debug=True, port=5000)

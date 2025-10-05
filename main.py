# Kingoo.py - MODIFIED for Login System

from flask import Flask, request, render_template_string, session, redirect, url_for
import os, requests, time, random, string, json, atexit
from threading import Thread, Event, Lock
from datetime import datetime

# --- Configuration & Initialization ---
app = Flask(__name__)
# It's highly recommended to use environment variables for secret keys in production
# This is crucial for session security - CHANGE THIS KEY!
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'VERY_SECRET_KEY_FOR_SESSIONS_12345') 
app.debug = True

# --- User Credentials (CHANGE THESE!) ---
# In a real app, use a database and securely hashed passwords (like using Flask-Bcrypt)
# For this simple example, we use a dictionary.
USERS = {
    'waleedking': '12345678', # <--- CHANGE THIS USERNAME AND PASSWORD
    # Add more users here if needed: 'user2': 'password2'
}

# Standard headers (rest of the script is largely unchanged until the routes)
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Connection': 'keep-alive'
}

# --- Global State ---
stop_events, threads, active_users = {}, {}, {}
TASK_FILE = 'tasks.json'
state_lock = Lock() # Lock for thread-safe access to active_users and saving

# --- Persistence Functions (Unchanged) ---
def save_tasks():
    # ... (same as original script)
    with state_lock:
        try:
            with open(TASK_FILE, 'w', encoding='utf-8') as f:
                # Only store essential, serializable data
                serializable_users = {
                    tid: {
                        k: v for k, v in info.items() 
                        if k in ['name', 'tokens_all', 'thread_id', 'msgs', 'delay', 'status', 'start_time', 'fb_name', 'msg_count']
                    }
                    for tid, info in active_users.items()
                }
                json.dump(serializable_users, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving tasks: {e}")

def load_tasks():
    # ... (same as original script)
    if not os.path.exists(TASK_FILE):
        return
    
    with state_lock:
        try:
            with open(TASK_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for tid, info in data.items():
                    active_users[tid] = info
                    # Check if status is ACTIVE and relevant keys exist before attempting restart
                    if info.get('status') == 'ACTIVE' and info.get('tokens_all') and info.get('thread_id') and info.get('msgs'):
                        print(f"Restarting task: {tid}")
                        stop_events[tid] = Event()
                        
                        # Restart the thread
                        th = Thread(
                            target=send_messages,
                            args=(info['tokens_all'], info['thread_id'], info.get('name', 'Hater'), info['delay'], info['msgs'], tid),
                            daemon=True
                        )
                        th.start()
                        threads[tid] = th
                    else:
                        # Mark tasks that can't be restarted as STOPPED to clear them on next save
                        active_users[tid]['status'] = 'STOPPED' 
        except Exception as e:
            print(f"Error loading tasks: {e}")


# Register save function to run when the application exits
atexit.register(save_tasks)
load_tasks()

# --- Facebook API Functions (Unchanged) ---
def fetch_profile_name(token: str) -> str:
    # ... (same as original script)
    try:
        # Simple request to check token validity and get name
        res = requests.get(f'https://graph.facebook.com/v19.0/me?access_token={token}', timeout=8)
        if res.status_code == 200:
            data = res.json()
            return data.get('name', 'Unknown')
        # If token is invalid (e.g., expired), Facebook often returns 400/403
        print(f"Token validation failed with status {res.status_code}: {res.text[:100]}")
        return 'INVALID TOKEN'
    except Exception as e:
        print(f"Error fetching profile name: {e}")
        return 'Unknown'

# --- Core Sending Logic (Unchanged) ---
def send_messages(tokens, thread_id, hater_name, base_delay, messages, task_id):
    # ... (same as original script)
    ev = stop_events.get(task_id)
    if not ev:
        print(f"Task {task_id} started without an event. Shutting down.")
        return
        
    tok_i, msg_i = 0, 0
    token_count, message_count = len(tokens), len(messages)
    
    # Update message count (This part is not in the original, but useful for logs)
    with state_lock:
        if task_id in active_users:
            active_users[task_id]['msg_sent'] = 0
            
    while not ev.is_set():
        try:
            if not tokens or not messages:
                print(f"Task {task_id} has no tokens or messages. Stopping.")
                break
                
            current_token = tokens[tok_i % token_count]
            original_message = messages[msg_i % message_count]
            
            # 1. Message Uniqueness (Evasion Technique)
            unique_tag = ''.join(random.choices(string.hexdigits, k=8))
            unique_message = f"{hater_name} {original_message} | {unique_tag}"
            
            # The actual message payload being sent
            payload = {
                'access_token': current_token,
                'message': unique_message
            }
            
            response = requests.post(
                f'https://graph.facebook.com/v19.0/t_{thread_id}/',
                data=payload,
                headers=headers,
                timeout=10
            )
            
            # --- Status Check and Logging ---
            if response.status_code == 200:
                print(f"Task {task_id} - Sent OK (Token {tok_i % token_count}). Message: '{original_message[:20]}...'")
                with state_lock:
                     if task_id in active_users:
                        active_users[task_id]['msg_sent'] = active_users[task_id].get('msg_sent', 0) + 1
            else:
                # Log detailed error for failed send
                error_info = response.json().get('error', {}).get('message', 'No message body.')
                print(f"Task {task_id} - Send FAILED (Token {tok_i % token_count}) - Status: {response.status_code}. Error: {error_info[:100]}...")

        except requests.exceptions.Timeout:
            print(f"Task {task_id} - Send FAILED: Request timed out.")
        except Exception as e:
            print(f"Task {task_id} - Unknown Error sending message: {e}")
        
        # 2. Cycle to the next token and message
        tok_i += 1
        msg_i += 1
        
        # 3. Delay Jitter (Anti-Rate Limit/Anti-Bot Technique)
        jitter_delay = base_delay + random.uniform(0.1, 1.0)
        
        # Check if stop event is set during the sleep
        if ev.wait(jitter_delay):
            break

# --- HTML Templates (Modified to include the Login HTML) ---

LOGIN_HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>WALEED KING - Login</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }
        body {
            background: linear-gradient(135deg, #1a2f1a 0%, #0d1f0d 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            color: white;
        }
        .login-container {
            width: 90%;
            max-width: 400px;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 15px;
            padding: 40px 30px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            text-align: center;
        }
        .title {
            font-size: 2rem;
            font-weight: bold;
            color: white;
            margin-bottom: 20px;
        }
        .form-group {
            margin-bottom: 20px;
            text-align: left;
        }
        label {
            display: block;
            margin-bottom: 8px;
            font-weight: 600;
            color: white;
        }
        input {
            width: 100%;
            padding: 12px 15px;
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 8px;
            background: rgba(255, 255, 255, 0.1);
            color: white;
            font-size: 16px;
        }
        .btn {
            background: linear-gradient(135deg, #ff4444 0%, #cc0000 100%);
            color: white;
            border: none;
            padding: 15px 30px;
            border-radius: 8px;
            font-size: 16px;
            font-weight: bold;
            cursor: pointer;
            width: 100%;
            transition: all 0.3s ease;
        }
        .btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(255, 68, 68, 0.4);
        }
        .error-box {
            background: linear-gradient(135deg, #c62828 0%, #b71c1c 100%);
            padding: 10px;
            border-radius: 8px;
            margin-bottom: 20px;
            font-size: 0.9rem;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <h1 class="title">👑 WALEED KING LOGIN 👑</h1>
        {{ msg_html | safe }}
        <form method="POST">
            <div class="form-group">
                <label for="username">Username:</label>
                <input type="text" id="username" name="username" required>
            </div>
            <div class="form-group">
                <label for="password">Password:</label>
                <input type="password" id="password" name="password" required>
            </div>
            <button type="submit" class="btn">🔑 LOGIN</button>
        </form>
    </div>
</body>
</html>
'''

# The original HTML template is still needed for the home route, so we keep it.
# (If you put the original HTML here, it would be identical to the one in the prompt)
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>WALEED KING - Message Sender</title>
    <style>
        /* ... (Original CSS from the prompt remains here) ... */
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }
        
        body {
            background: linear-gradient(135deg, #1a2f1a 0%, #0d1f0d 100%);
            min-height: 100vh;
            padding: 20px;
            color: white;
        }
        
        .container {
            max-width: 800px;
            margin: 0 auto;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 15px;
            padding: 30px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
        }
        
        .header {
            text-align: center;
            margin-bottom: 30px;
            padding: 20px;
            background: rgba(255, 255, 255, 0.08);
            border-radius: 10px;
        }
        
        .title {
            font-size: 2.5rem;
            font-weight: bold;
            color: white;
            margin-bottom: 10px;
        }
        
        .subtitle {
            color: #ccc;
            font-size: 1.1rem;
        }
        
        .form-section {
            background: rgba(255, 255, 255, 0.08);
            padding: 25px;
            border-radius: 10px;
            margin-bottom: 20px;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }
        
        .form-group {
            margin-bottom: 20px;
        }
        
        label {
            display: block;
            margin-bottom: 8px;
            font-weight: 600;
            color: white;
        }
        
        input, select, textarea {
            width: 100%;
            padding: 12px 15px;
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 8px;
            background: rgba(255, 255, 255, 0.1);
            color: white;
            font-size: 14px;
        }
        
        input::placeholder, textarea::placeholder {
            color: rgba(255, 255, 255, 0.6);
        }
        
        input:focus, select:focus, textarea:focus {
            outline: none;
            border-color: #4CAF50;
            box-shadow: 0 0 5px rgba(76, 175, 80, 0.3);
        }
        
        .btn {
            background: linear-gradient(135deg, #ff4444 0%, #cc0000 100%);
            color: white;
            border: none;
            padding: 15px 30px;
            border-radius: 8px;
            font-size: 16px;
            font-weight: bold;
            cursor: pointer;
            width: 100%;
            transition: all 0.3s ease;
        }
        
        .btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(255, 68, 68, 0.4);
        }
        
        .success-box {
            background: linear-gradient(135deg, #2e7d32 0%, #1b5e20 100%);
            padding: 20px;
            border-radius: 10px;
            margin: 20px 0;
            border: 1px solid #4CAF50;
        }
        
        .error-box {
            background: linear-gradient(135deg, #c62828 0%, #b71c1c 100%);
            padding: 20px;
            border-radius: 10px;
            margin: 20px 0;
            border: 1px solid #f44336;
        }
        
        .success-title {
            font-size: 1.3rem;
            font-weight: bold;
            margin-bottom: 10px;
        }
        
        .stop-key {
            background: rgba(0, 0, 0, 0.3);
            padding: 15px;
            border-radius: 8px;
            font-family: monospace;
            font-size: 16px;
            margin: 10px 0;
            word-break: break-all;
        }
        
        .section-title {
            font-size: 1.4rem;
            font-weight: bold;
            margin-bottom: 15px;
            color: white;
            border-bottom: 2px solid rgba(255, 255, 255, 0.2);
            padding-bottom: 10px;
        }
        
        .radio-group {
            display: flex;
            gap: 20px;
            margin-bottom: 15px;
        }
        
        .radio-item {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .radio-item input[type="radio"] {
            width: auto;
        }
        
        .file-input {
            padding: 10px;
        }
        
        .instructions {
            background: rgba(255, 255, 255, 0.05);
            padding: 15px;
            border-radius: 8px;
            margin-top: 20px;
            font-size: 0.9rem;
        }
        
        .instructions ul {
            margin-left: 20px;
            margin-top: 10px;
        }
        
        .instructions li {
            margin-bottom: 5px;
        }
        .logout-link {
            text-align: right;
            margin-bottom: 10px;
        }
        .logout-link a {
            color: #ff4444;
            text-decoration: none;
            font-weight: bold;
        }
        .logout-link a:hover {
            color: #cc0000;
        }

    </style>
</head>
<body>
    <div class="container">
        <div class="logout-link">
            Logged in as: <b>{{ session.get('username', 'Guest') }}</b> | <a href="/logout">Logout</a>
        </div>
        <div class="header">
            <h1 class="title">👑 WALEED KING MESSAGE SENDER 👑</h1>
            <p class="subtitle">Advanced Facebook Message Sending System</p>
        </div>
        
        <div class="form-section">
            <h2 class="section-title">🚀 START NEW TASK</h2>
            <form method="POST" enctype="multipart/form-data">
                <div class="form-group">
                    <label>Token Option:</label>
                    <div class="radio-group">
                        <div class="radio-item">
                            <input type="radio" id="single" name="tokenOption" value="single" checked>
                            <label for="single">Single Token</label>
                        </div>
                        <div class="radio-item">
                            <input type="radio" id="multiple" name="tokenOption" value="multiple">
                            <label for="multiple">Multiple Tokens (File)</label>
                        </div>
                    </div>
                </div>
                
                <div class="form-group" id="singleTokenGroup">
                    <label for="singleToken">Facebook Token:</label>
                    <input type="text" id="singleToken" name="singleToken" placeholder="Enter your Facebook token...">
                </div>
                
                <div class="form-group" id="tokenFileGroup" style="display: none;">
                    <label for="tokenFile">Token File (TXT):</label>
                    <input type="file" id="tokenFile" name="tokenFile" accept=".txt" class="file-input">
                </div>
                
                <div class="form-group">
                    <label for="threadId">Thread ID:</label>
                    <input type="text" id="threadId" name="threadId" placeholder="Enter conversation/thread ID..." required>
                </div>
                
                <div class="form-group">
                    <label for="kidx">Prefix Name:</label>
                    <input type="text" id="kidx" name="kidx" placeholder="Enter prefix name (e.g., Target's Name)..." required>
                </div>
                
                <div class="form-group">
                    <label for="time">Base Delay (seconds):</label>
                    <input type="number" id="time" name="time" value="2" min="1" max="60" required>
                    <small style="color: #bbb;">(Actual delay will have a small random addition for anti-block)</small>
                </div>
                
                <div class="form-group">
                    <label for="txtFile">Messages File (TXT):</label>
                    <input type="file" id="txtFile" name="txtFile" accept=".txt" class="file-input" required>
                </div>
                
                <button type="submit" name="startTask" class="btn">🚀 START MESSAGING</button>
            </form>
        </div>
        
        <div class="form-section">
            <h2 class="section-title">⏹️ STOP TASK</h2>
            <form method="POST">
                <div class="form-group">
                    <label for="taskId">Stop Key:</label>
                    <input type="text" id="taskId" name="taskId" placeholder="Enter your stop key...">
                </div>
                <button type="submit" name="stopTask" class="btn">🛑 STOP TASK</button>
            </form>
        </div>
        
        {{ msg_html | safe }}
        {{ stop_html | safe }}
        
        <div class="instructions">
            <h3>📋 Instructions:</h3>
            <ul>
                <li>For single token: Enter one Facebook token.</li>
                <li>For multiple tokens: Upload a .txt file with one token per line (Recommended for anti-block).</li>
                <li>Thread ID: Get from Facebook conversation URL.</li>
                <li>Prefix Name: This will be prefixed to each message.</li>
                <li>Base Delay: Time between messages in seconds (The system adds a random value to this).</li>
                <li>Messages File: .txt file with one message per line.</li>
                <li>Save your **STOP KEY** to terminate the task later.</li>
                <li>**Anti-Block Tip:** Use multiple tokens and longer delays (e.g., 5s+) for best results.</li>
            </ul>
        </div>
    </div>

    <script>
        // Toggle between single and multiple tokens
        document.addEventListener('DOMContentLoaded', function() {
            const singleRadio = document.getElementById('single');
            const multipleRadio = document.getElementById('multiple');
            const singleGroup = document.getElementById('singleTokenGroup');
            const tokenFileGroup = document.getElementById('tokenFileGroup');
            
            function toggleTokenInput() {
                if (singleRadio.checked) {
                    singleGroup.style.display = 'block';
                    tokenFileGroup.style.display = 'none';
                } else {
                    singleGroup.style.display = 'none';
                    tokenFileGroup.style.display = 'block';
                }
            }
            
            singleRadio.addEventListener('change', toggleTokenInput);
            multipleRadio.addEventListener('change', toggleTokenInput);
            toggleTokenInput(); // Initial call
        });
    </script>
</body>
</html>
'''


# --- Flask Routes ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Handles user login."""
    msg_html = ""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # Simple credential check
        if username in USERS and USERS[username] == password:
            session['logged_in'] = True
            session['username'] = username
            return redirect(url_for('home'))
        else:
            msg_html = "<div class='error-box'>❌ Invalid Username or Password!</div>"
    
    return render_template_string(LOGIN_HTML_TEMPLATE, msg_html=msg_html)

@app.route('/logout')
def logout():
    """Clears the session and redirects to login."""
    session.pop('logged_in', None)
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/', methods=['GET', 'POST'])
def home():
    """Main application route, now protected by login."""
    # --- AUTHENTICATION CHECK ---
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    # --------------------------

    msg_html = stop_html = ""
    
    if request.method == 'POST':
        # Start Task
        if 'startTask' in request.form:
            try:
                # --- Token Handling ---
                token_option = request.form.get('tokenOption', 'single')
                tokens = []
                
                if token_option == 'single':
                    single_token = request.form.get('singleToken', '').strip()
                    if single_token:
                        tokens = [single_token]
                else:  # multiple
                    token_file = request.files.get('tokenFile')
                    if token_file and token_file.filename:
                        content = token_file.read().decode('utf-8', errors='ignore')
                        tokens = [t.strip() for t in content.splitlines() if t.strip()]
                
                # --- Validation ---
                thread_id = request.form.get('threadId', '').strip()
                hater_name = request.form.get('kidx', '').strip()
                # Ensure delay is at least 1 second for basic anti-block
                base_delay = max(int(request.form.get('time', 2) or 2), 1) 
                
                # Get messages
                msg_file = request.files.get('txtFile')
                if not msg_file or not msg_file.filename:
                    msg_html = "<div class='error-box'>❌ Please select a messages file!</div>"
                    return render_template_string(HTML_TEMPLATE, msg_html=msg_html, stop_html=stop_html, session=session)
                    
                content = msg_file.read().decode('utf-8', errors='ignore')
                messages = [m.strip() for m in content.splitlines() if m.strip()]
                
                if not tokens:
                    msg_html = "<div class='error-box'>❌ No valid tokens provided!</div>"
                elif not thread_id:
                    msg_html = "<div class='error-box'>❌ Thread ID is required!</div>"
                elif not hater_name:
                    msg_html = "<div class='error-box'>❌ Prefix Name is required!</div>"
                elif not messages:
                    msg_html = "<div class='error-box'>❌ No valid messages found in file!</div>"
                else:
                    # Validate the first token and get the FB profile name
                    fb_name = fetch_profile_name(tokens[0])
                    if fb_name == 'INVALID TOKEN':
                        msg_html = "<div class='error-box'>❌ The first token provided is invalid or expired!</div>"
                        return render_template_string(HTML_TEMPLATE, msg_html=msg_html, stop_html=stop_html, session=session)

                    # --- Task Creation ---
                    task_id = 'waleed_' + ''.join(random.choices(string.ascii_letters + string.digits, k=12))
                    
                    with state_lock:
                        stop_events[task_id] = Event()
                        
                        # Start thread
                        th = Thread(
                            target=send_messages,
                            args=(tokens, thread_id, hater_name, base_delay, messages, task_id),
                            daemon=True
                        )
                        th.start()
                        threads[task_id] = th
                        
                        # Save task info
                        active_users[task_id] = {
                            'name': hater_name,
                            'tokens_all': tokens,
                            'fb_name': fb_name,
                            'thread_id': thread_id,
                            'msg_file': msg_file.filename,
                            'msgs': messages,
                            'delay': base_delay,
                            'msg_count': len(messages),
                            'status': 'ACTIVE',
                            'start_time': datetime.now().isoformat()
                        }
                        save_tasks()
                        
                        msg_html = f"""
                        <div class='success-box'>
                            <div class='success-title'>✅ TASK STARTED SUCCESSFULLY</div>
                            <div class='stop-key'>{task_id}</div>
                            <p><strong>Token User: {fb_name}</strong></p>
                            <p><strong>Save this STOP KEY to terminate the task later</strong></p>
                            <p>Thread ID: {thread_id} | Tokens: {len(tokens)} | Messages: {len(messages)} | Base Delay: {base_delay}s</p>
                        </div>
                        """
                        
            except Exception as e:
                msg_html = f"<div class='error-box'>❌ Error starting task: {str(e)}</div>"
        
        # Stop Task
        elif 'stopTask' in request.form:
            task_id = request.form.get('taskId', '').strip()
            
            with state_lock:
                if task_id in stop_events:
                    stop_events[task_id].set() # Signal the thread to stop
                    
                    # Update status in the persistent storage
                    if task_id in active_users:
                        active_users[task_id]['status'] = 'STOPPED'
                        active_users[task_id]['stop_time'] = datetime.now().isoformat()
                        # Optional: Wait briefly for thread to terminate before removing it from list
                        # threads.get(task_id).join(timeout=1) 
                        if task_id in threads:
                           del threads[task_id]
                           
                    save_tasks()
                    stop_html = f"""
                    <div class='success-box'>
                        <div class='success-title'>⏹️ TASK STOPPED</div>
                        <div class='stop-key'>{task_id}</div>
                        <p>Task has been successfully terminated</p>
                    </div>
                    """
                else:
                    stop_html = "<div class='error-box'>❌ INVALID STOP KEY or Task already stopped.</div>"
    
    # Pass 'session' to the template so we can display the username and logout link
    return render_template_string(HTML_TEMPLATE, msg_html=msg_html, stop_html=stop_html, session=session)

if __name__ == '__main__':
    print("--- WALEED KING SERVER STARTING ---")
    print("Access the LOGIN page at: http://127.0.0.1:21584/login")
    print("Default User: waleedking | Default Pass: 12345678 (CHANGE THESE!)")
    app.run(host='0.0.0.0', port=21584, debug=True)

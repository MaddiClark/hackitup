import json
import sqlite3
from time import asctime, strftime, strptime

from dotenv import dotenv_values
from flask import Flask, redirect, render_template, request, url_for
from flask_socketio import SocketIO
from google import genai

config = dotenv_values(".env")
GEMINI_API_KEY = config["GEMINI_API_KEY"]

app = Flask(__name__)
socketio = SocketIO(app)

client = genai.Client(api_key=GEMINI_API_KEY)


# Connect to SQLite Database
def get_db_connection():
    conn = sqlite3.connect("data.db")  # 'data.db' is the name of the SQLite database
    conn.row_factory = sqlite3.Row  # This allows us to access rows as dictionaries
    return conn


# Builds the contidtions data base (only one so far)
def init_db():
    conn = get_db_connection()

    # The sql data base column build
    # IMPORTANT: Remeber to delete or manually add onto database if a new entry is introduced, EG
    conn.execute("""CREATE TABLE IF NOT EXISTS items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        value INTEGER NOT NULL,
                        date_time TEXT NOT NULL
                    )""")

    conn.execute("""CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        sender TEXT NOT NULL,
                        content TEXT NOT NULL,
                        date_time TEXT NOT NULL
                    )""")

    conn.commit()
    conn.close()


@app.route("/")
def home():
    return render_template("index.html")  # The homepage that links to `/patient`


@app.route("/")
def start():
    return render_template("index.html")  # The homepage that links to `/patient`


@app.route("/sign-in", methods=["GET", "POST"])
def sign_in():
    # Mock sign-in (no actual login functionality)
    if request.method == "POST":
        # Normally, you would check the credentials here, but we will just mock the process, EG.
        return redirect(url_for("patient"))  # Redirect to patient page after submitting

    return render_template(
        "sign_in.html"
    )  # Render sign-in page when accessed by GET request


@app.route("/patient")
def patient():
    conn = get_db_connection()
    items = conn.execute("SELECT * FROM items").fetchall()  # Fetch all items from DB

    # Fetch all messages from DB
    messages = conn.execute("SELECT * FROM messages").fetchall()

    conn.close()
    return render_template("patient.html", items=items, messages=messages)


@app.route("/doctor-sign-in", methods=["GET", "POST"])
def doctor_sign_in():
    # Mock sign-in (no actual login functionality)
    if request.method == "POST":
        # Normally, you would check the credentials here, but we will just mock the process.
        return redirect(url_for("doctor"))  # Redirect to doctor page after submitting

    return render_template(
        "doctor_sign_in.html"
    )  # Render doctor sign-in page when accessed by GET request


@app.post("/add")
def add_item():
    item_name = request.form["item_name"]  # User can input the condition name
    item_value = request.form["item_value"]  # Get the value of the slider from the form

    # Get the date and time from the form
    date_time = request.form["date_time"]
    date_time = strptime(date_time, "%Y-%m-%dT%H:%M")
    # Format into friendlier version
    date_time = strftime("%c", date_time)

    if item_name and item_value and date_time:  # Make sure all fields are filled
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO items (name, value, date_time) VALUES (?, ?, ?)",
                (item_name, item_value, date_time),
            )
            conn.commit()
        update_client_data()
    return redirect(url_for("patient"))  # Redirect to patient page after adding item


@app.route("/delete/<int:item_id>", methods=["GET"])
def delete_item(item_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    update_client_data()
    return redirect(url_for("patient"))  # Redirect to patient page after deleting item


@app.route("/doctor", methods=["GET"])
def doctor():
    severity_filter = request.args.get(
        "severity"
    )  # Get the severity filter from the URL
    name_filter = request.args.get("name_filter")  # Get the name filter from the URL
    conn = get_db_connection()

    # Build the SQL query with conditions based on the filters
    query = "SELECT * FROM items"
    params = []

    # Apply severity filter if present
    if severity_filter:
        query += " WHERE value = ?"
        params.append(severity_filter)

    # Apply name filter if present
    if name_filter:
        if params:
            query += " AND name LIKE ?"
        else:
            query += " WHERE name LIKE ?"
        params.append(f"%{name_filter}%")

    # Execute the query with the filters
    items = conn.execute(query, params).fetchall()

    # Fetch all messages from DB
    messages = conn.execute("SELECT * FROM messages").fetchall()

    conn.close()
    return render_template("doctor.html", items=items, messages=messages)


def update_db(sender: str, content: str | None = None):
    if content is None:
        # Get message content if not provided
        content = request.form["content"]
    date_time = strftime("%I:%M %p")
    # Insert message into db
    with get_db_connection() as conn:
        _ = conn.execute(
            "INSERT INTO messages (sender, content, date_time) VALUES (?, ?, ?)",
            (sender, content, date_time),
        )
        conn.commit()


def update_client_data():
    # Tell all connected clients to update chat history
    socketio.emit("update")


@app.post("/patient/chat")
def patient_chat():
    update_db("patient")
    update_client_data()
    return redirect(url_for("patient") + "#chat-form")


@app.post("/doctor/chat")
def doctor_chat():
    update_db("doctor")
    update_client_data()
    return redirect(url_for("doctor") + "#chat-form")


@app.post("/patient/gemini")
def patient_gemini():
    content = ask_gemini()
    update_db("gemini", content)
    update_client_data()
    return redirect(url_for("patient") + "#chat-form")


def ask_gemini():
    with get_db_connection() as conn:
        # Fetch all conditions from db
        items = conn.execute(
            "SELECT * FROM items"
        ).fetchall()  # Fetch all items from DB
        # Format conditions into json list
        conditions = json.dumps(
            [
                {
                    "description": condition["name"],
                    "severity": condition["value"],
                    "date": condition["date_time"],
                }
                for condition in items
            ]
        )

        cur_time = asctime()

    # Prompt Gemini with condition list
    prompt = "The user will input a JSON list of health conditions they have experienced. Each element in the list is an object with a **description** of the issue, a provided **severity**, and a **date** when the issue first arose. The current date is {}. Respond with a paragraph for each unique health condition that the user experienced, providing further research into the health issue and an assessment of the severity based on the user's description, rated severity, and the time since issue occured. Do not include any bold or italics text markup. Separate new lines with double <br>. Do not include any other information. The issues the user experienced are: {}".format(
        cur_time, conditions
    )

    response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)

    return str(response.text)


if __name__ == "__main__":
    init_db()  # Initialize the database
    socketio.run(app, debug=True, host="0.0.0.0", port=5000)
    # for opening to local public wifi, EG

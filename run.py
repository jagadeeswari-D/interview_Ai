"""InterviewIQ application entry point.

Run with:
    python run.py

The app auto-creates the SQLite database (instance/interviewiq.db) on startup.
"""

from dotenv import load_dotenv

load_dotenv()

from interview_Ai.app import create_app

app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=app.config["DEBUG"])

"""Submission-only listener; shares storage, never mounts admin routes."""
from app import create_submission_app

if __name__ == '__main__':
    create_submission_app().run(host='127.0.0.1', port=8742, debug=False)

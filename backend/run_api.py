import os
from waitress import serve
from app import create_app

if __name__ == "__main__":
    os.umask(0o077)
    serve(create_app(), host=os.getenv("BIND_HOST","127.0.0.1"),
          port=int(os.getenv("PORT","8000")), threads=12, max_request_body_size=6*1024*1024)

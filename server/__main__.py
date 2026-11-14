import os

import uvicorn

from server.app import app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("RC_PORT") or 8787))

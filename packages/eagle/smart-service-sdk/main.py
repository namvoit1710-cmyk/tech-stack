import uvicorn
from smart_service_sdk import smart_create_app


app = smart_create_app()


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8080)

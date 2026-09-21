FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Local editable deps from requirements.txt, placed at the absolute paths their
# relative references resolve to from /app:
#   `-e ../../agent/agent-sdk`                         -> /agent/agent-sdk
#   agent-sdk's `simplemdg-mcp @ file:../../../../libs/mcp/python` -> /libs/mcp/python
# (provided as named build contexts in deployment.yml).
COPY --from=agent-sdk . /agent/agent-sdk
COPY --from=mcp . /libs/mcp/python

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN adduser --disabled-password --gecos '' appuser
USER appuser

EXPOSE 8050

CMD ["python", "main.py"]

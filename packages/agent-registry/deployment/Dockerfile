FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Local editable deps from requirements.txt, placed at the absolute paths their
# relative references resolve to from /app:
#   `-e ../../agent/agent-sdk`                         -> /agent/agent-sdk
#   agent-sdk's `simplemdg-mcp @ file:../../../../libs/mcp/python` -> /libs/mcp/python
# (provided as named build contexts in deployment.yml).
COPY --from=agent-sdk . /agent/agent-sdk
COPY --from=mcp . /libs/mcp/python

COPY requirements.txt ./requirements.txt

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN sed -i 's/\r$//' /app/deployment/migrate.sh /app/deployment/entrypoint.sh \
	&& chmod +x /app/deployment/migrate.sh /app/deployment/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/deployment/entrypoint.sh"]
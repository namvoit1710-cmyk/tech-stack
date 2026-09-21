FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Sibling editable dep (-e ../agent-sdk) and its nested local dep
# (simplemdg-mcp @ file:../../../../libs/mcp/python). Placed at the paths
# those relative references resolve to from /app.
COPY --from=agent-sdk . /agent-sdk
COPY --from=mcp . /libs/mcp/python

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN adduser --disabled-password --gecos '' appuser
USER appuser

EXPOSE 8005

CMD ["python", "main.py"]

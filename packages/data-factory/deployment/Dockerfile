# Use an official lightweight Python image
FROM python:3.11-slim

# Set environment variables
# PYTHONDONTWRITEBYTECODE: Prevents Python from writing .pyc files to disc
# PYTHONUNBUFFERED: Ensures logs are flushed immediately to the console
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory in the container
WORKDIR /app

# 1. Copy dependencies first to leverage Docker cache
COPY requirements.txt .

# 2. Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# 3. Copy the MCP library from shared libs
COPY --from=libs /mcp/python /app/libs/mcp/python

# 4. Copy the rest of the application code
COPY . .

# Security: Create a non-root user to run the app
RUN adduser --disabled-password --gecos '' appuser
USER appuser

# Expose the ports we might use
# 8000 for FastAPI (REST)
# 50051 for gRPC
EXPOSE 8000


# Default command (Can be overridden by docker-compose)
CMD ["python", "main.py"]
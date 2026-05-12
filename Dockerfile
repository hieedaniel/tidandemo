FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .
COPY core/ ./core/
COPY data/ ./data/
COPY products/ ./products/
COPY CLAUDE.md .

# Keep a factory copy of the default config outside the data/ volume mount path.
# When the server mounts -v ./data:/app/data, this copy survives and is used
# by DataManager to auto-initialize data/default_config.json on first startup.
COPY data/default_config.json ./default_config.json

# Expose Streamlit port
EXPOSE 8501

# Set environment variables
ENV STREAMLIT_SERVER_PORT=8501
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0

# Healthcheck
HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health || exit 1

# Run the application
CMD ["streamlit", "run", "app.py", "--server.headless", "true"]
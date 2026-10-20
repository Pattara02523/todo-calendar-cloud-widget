FROM python:3.12-slim

WORKDIR /app

# Copy application files
COPY . /app

# Expose server port (Render & Railway automatically bind PORT)
ENV PORT=18234
EXPOSE 18234

# Run background sync & web server
CMD ["python", "desktop_sync_server.py"]

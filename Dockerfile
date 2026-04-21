FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt flask

COPY . .

EXPOSE 3456
CMD ["python3", "server.py"]

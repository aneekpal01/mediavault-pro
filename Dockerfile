FROM python:3.11-slim

# ffmpeg install karo (MP3 conversion ke liye)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies install karo
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Saari files copy karo
COPY . .

# Downloads folder banao
RUN mkdir -p downloads

# Port expose karo
EXPOSE 8000

# Server start karo
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
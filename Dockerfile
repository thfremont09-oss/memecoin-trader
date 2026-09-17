FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x docker/entrypoint.sh

ENV MEMECOIN_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8787

ENTRYPOINT ["docker/entrypoint.sh"]

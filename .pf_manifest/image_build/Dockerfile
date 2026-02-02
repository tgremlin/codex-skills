FROM python:3.11-slim
RUN apt-get update && apt-get install -y git build-essential curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
COPY . /workspace
RUN python -m pip install -r requirements.txt

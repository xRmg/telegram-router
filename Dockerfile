FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY examples ./examples

RUN pip install .

CMD ["python", "-m", "telegram_proxy"]

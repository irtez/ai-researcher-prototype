FROM python:3.12-slim
RUN addgroup --system app && adduser --system --ingroup app --disabled-password app

WORKDIR /opt/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=app:app app.py tools.py ./

ENV PYTHONUNBUFFERED=1 \
    PYTHONOPTIMIZE=2 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_INDEX=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=3 \
    PYTHONNOUSERSITE=1
USER app:app
EXPOSE 8000
CMD [ \
    "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", \
    "--no-access-log", "--no-server-header" \
]
FROM python:3.13-slim

WORKDIR /app

COPY services/commun/requirements.txt services/commun/requirements.txt
COPY services/reservations/requirements.txt services/reservations/requirements.txt
RUN python -m pip install --no-cache-dir -r services/reservations/requirements.txt

COPY services/commun/ services/commun/
COPY services/reservations/ services/reservations/

CMD ["python", "-m", "uvicorn", "services.reservations.main:app", "--host", "0.0.0.0", "--port", "8000"]

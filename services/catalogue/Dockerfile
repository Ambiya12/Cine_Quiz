FROM python:3.13-slim

WORKDIR /app

COPY services/commun/requirements.txt services/commun/requirements.txt
COPY services/catalogue/requirements.txt services/catalogue/requirements.txt
RUN python -m pip install --no-cache-dir -r services/catalogue/requirements.txt

COPY services/commun/ services/commun/
COPY services/catalogue/ services/catalogue/

CMD ["python", "-m", "uvicorn", "services.catalogue.main:app", "--host", "0.0.0.0", "--port", "8000"]

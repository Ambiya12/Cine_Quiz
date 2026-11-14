FROM python:3.13-slim

WORKDIR /app

COPY services/commun/requirements.txt services/commun/requirements.txt
COPY services/fournisseurs/requirements.txt services/fournisseurs/requirements.txt
RUN python -m pip install --no-cache-dir -r services/fournisseurs/requirements.txt

COPY services/commun/ services/commun/
COPY services/fournisseurs/ services/fournisseurs/

CMD ["python", "-m", "uvicorn", "services.fournisseurs.main:app", "--host", "0.0.0.0", "--port", "8000"]

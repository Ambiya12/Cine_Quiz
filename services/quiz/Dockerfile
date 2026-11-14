FROM python:3.13-slim

WORKDIR /app

COPY services/commun/requirements.txt services/commun/requirements.txt
COPY services/quiz/requirements.txt services/quiz/requirements.txt
RUN python -m pip install --no-cache-dir -r services/quiz/requirements.txt

COPY services/commun/ services/commun/
COPY services/quiz/ services/quiz/

CMD ["python", "-m", "uvicorn", "services.quiz.main:app", "--host", "0.0.0.0", "--port", "8000"]

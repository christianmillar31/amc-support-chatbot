FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code and static files
COPY app/ ./app/
COPY static/ ./static/
COPY index_data/ ./index_data/
COPY amclogo.png ./amclogo.png
COPY AMC-Logo-Long.jpg ./AMC-Logo-Long.jpg

# Copy CSV database if it exists
COPY ["CM Servo Info.csv", "./CM Servo Info.csv"]

# Pre-download the embedding and reranker models so startup is fast
RUN python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; SentenceTransformer('BAAI/bge-base-en-v1.5'); CrossEncoder('cross-encoder/ms-marco-MiniLM-L-12-v2'); print('Models cached')"

# HF Spaces requires port 7860
EXPOSE 7860

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]

FROM python:3.11-slim

# Create non-root user (HF Spaces requirement)
RUN useradd -m -u 1000 user
ENV HOME=/home/user
ENV PATH="/home/user/.local/bin:$PATH"

WORKDIR /app

# Install dependencies as root first
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code and static files
COPY app/ ./app/
COPY static/ ./static/
COPY index_data/ ./index_data/
COPY amclogo.png ./amclogo.png
COPY AMC-Logo-Long.jpg ./AMC-Logo-Long.jpg
COPY ["CM Servo Info.csv", "./CM Servo Info.csv"]
COPY faq_index.csv ./faq_index.csv
COPY site_data/ ./site_data/

# Set model cache to writable location
ENV TRANSFORMERS_CACHE=/home/user/.cache/huggingface
ENV HF_HOME=/home/user/.cache/huggingface
ENV SENTENCE_TRANSFORMERS_HOME=/home/user/.cache/sentence-transformers

# Pre-download models as root, then move cache to user dir
RUN python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; SentenceTransformer('BAAI/bge-base-en-v1.5'); CrossEncoder('cross-encoder/ms-marco-MiniLM-L-12-v2'); print('Models cached')"

# Give user ownership of everything
RUN chown -R user:user /app /home/user

# Switch to non-root user
USER user

# HF Spaces requires port 7860
EXPOSE 7860

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
